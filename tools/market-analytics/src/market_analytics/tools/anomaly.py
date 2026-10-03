# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""market_anomaly_scan: score later sessions against a PCA model of an earlier baseline window.

The detector standardizes four price/volume features on the training window, fits a PCA with up to three
components, and scores each session by its reconstruction error. Sessions above the training window's 95th
percentile error are flagged. On GPU, cuml.accel runs the same scikit-learn PCA.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
from sklearn import config_context
from sklearn.decomposition import PCA

from ..data import FEATURES
from ..data import MarketData
from ..models import AnomalyObservation
from ..models import InvalidRequest
from ..models import MarketAnomalyPayload
from .common import Output
from .common import check_window
from .common import host

LIMITATIONS = (
    "Anomaly scores describe unusual observed feature combinations; they are not forecasts or probabilities.",
    "Observed deviations are robust z-scores against the training window, not returns or percentages; they are "
    "descriptive reason codes and do not establish a cause or adverse event.",
)
MIN_TRAINING_ROWS = 8
FLAG_QUANTILE = 0.95


def run(
    data: MarketData,
    *,
    universe_id: str,
    training_start: datetime,
    training_end: datetime,
    scoring_start: datetime,
    scoring_end: datetime,
    limit: int = 10,
    minimum_percentile: float | None = None,
) -> Output:
    check_window(training_start, training_end)
    check_window(scoring_start, scoring_end)
    if scoring_start <= training_end:
        raise InvalidRequest("the scoring window must begin after the training window ends")
    features = data.features
    scoped = features[features["asset_id"].isin(data.universe(universe_id))]
    training = scoped[scoped["timestamp"].between(training_start, training_end)]
    scoring = scoped[scoped["timestamp"].between(scoring_start, scoring_end)]
    if len(training) < MIN_TRAINING_ROWS:
        raise InvalidRequest(f"the training window must contain at least {MIN_TRAINING_ROWS} observations")
    if scoring.empty:
        raise InvalidRequest("the scoring window contains no observations")

    # Plain host arrays: PCA runs on them through cuml.accel, and the arithmetic below is small NumPy work that
    # cudf.pandas would otherwise route through its array proxy.
    train = np.asarray(training[list(FEATURES)].to_numpy(np.float64))
    score = np.asarray(scoring[list(FEATURES)].to_numpy(np.float64))
    mean, scale = train.mean(axis=0), train.std(axis=0)
    scale[scale <= 1e-12] = 1.0
    train_z, score_z = (train - mean) / scale, (score - mean) / scale
    # Checked once here, on the host, and not again inside PCA: cuml.accel's own check compiles a CUDA kernel for
    # each new input size (about 5 s each on an A100), which no warm-up can cover. The error is scikit-learn's.
    if not (np.isfinite(train_z).all() and np.isfinite(score_z).all()):
        raise ValueError("the anomaly features contain NaN or infinity")
    with config_context(assume_finite=True):
        model = PCA(n_components=min(3, len(FEATURES), len(train) - 1), svd_solver="full").fit(train_z)
        train_error = np.mean((train_z - model.inverse_transform(model.transform(train_z))) ** 2, axis=1)
        score_error = np.mean((score_z - model.inverse_transform(model.transform(score_z))) ** 2, axis=1)
    threshold = np.quantile(train_error, FLAG_QUANTILE)

    decision_score = threshold - score_error

    # Ranked on the host too: only the `limit` highest scores are needed, and on the GPU sorting the whole scoring
    # frame by three keys, then reading its top rows back, took more pandas calls than the PCA itself. The order is
    # the one that sort gave (score descending, then asset id, then time), and a percentile still counts every row.
    count = len(score_error)
    top, asset_ids, timestamps = _highest(scoring, score_error, min(limit, count))
    position = np.arange(len(top))
    cohort_percentile = 100 * (count - position) / count
    if minimum_percentile is not None:
        kept = cohort_percentile >= minimum_percentile  # it falls with the position, so the kept rows lead
        top, asset_ids, timestamps = top[kept], asset_ids[kept], timestamps[kept]
        position, cohort_percentile = position[kept], cohort_percentile[kept]

    # Robust z-scores (median / MAD) against the training window explain which features moved.
    median = np.median(train, axis=0)
    mad = np.median(np.abs(train - median), axis=0) * 1.4826
    deviations = (score[top] - median) / np.where(mad > 1e-12, mad, 1.0)
    observations = [
        AnomalyObservation(
            rank=rank,
            asset_id=asset_id,
            timestamp=timestamp,
            anomaly_score=anomaly_score,
            decision_score=decision,
            cohort_percentile=percentile,
            is_anomaly=decision < 0,
            observed_deviations=dict(zip(FEATURES, row_deviations, strict=True)),
        )
        for rank, asset_id, timestamp, anomaly_score, decision, percentile, row_deviations in zip(
            (position + 1).tolist(),
            asset_ids.tolist(),
            timestamps.astype("datetime64[us]").tolist(),
            score_error[top].tolist(),
            decision_score[top].tolist(),
            cohort_percentile.tolist(),
            deviations.tolist(),
            strict=True,
        )
    ]
    payload = MarketAnomalyPayload(
        universe_id=universe_id,
        feature_names=list(FEATURES),
        training_observations=len(training),
        scoring_observations=len(scoring),
        flagged_observations=int((score_error > threshold).sum()),
        observations=observations,
    )
    # Concatenated, then counted: both windows' assets, on the GPU under cudf.pandas
    assets = pd.concat([training["asset_id"], scoring["asset_id"]]).nunique()
    return Output(payload, rows_scanned=len(training) + len(scoring), assets=assets, empty=not observations)


def _highest(scoring: pd.DataFrame, scores: np.ndarray, limit: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The positions of the `limit` highest scores, in rank order, with their asset ids and timestamps.

    Only the rows that can rank are read back: those scoring at least the `limit`-th highest score, ties included,
    which the asset id and time then order exactly as a sort of every row would.
    """
    if limit <= 0:
        return np.array([], dtype=np.int64), np.array([], dtype=object), np.array([], dtype="datetime64[ns]")
    cutoff = np.partition(scores, len(scores) - limit)[len(scores) - limit]
    candidates = np.flatnonzero(scores >= cutoff)
    rows = scoring.iloc[candidates]
    asset_ids, timestamps = host(rows["asset_id"]), host(rows["timestamp"])
    order = np.lexsort((timestamps, asset_ids, -scores[candidates]))[:limit]
    return candidates[order], asset_ids[order], timestamps[order]
