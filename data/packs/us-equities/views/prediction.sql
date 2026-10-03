-- SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
-- SPDX-License-Identifier: Apache-2.0
--
-- Leakage-safe prediction views for NVIDIA Kumo. Only facts observable at the anchor are visible.
-- demo-data fills in the double-brace placeholders from the `prediction` section of pack.yaml.
-- The entities are the 50 most liquid stocks; liquidity_rank uses only sessions up to the anchor.
CREATE SCHEMA IF NOT EXISTS prediction;
CREATE VIEW prediction.asset_entities AS
SELECT a.asset_id, a.company_name, a.sector, a.industry, a.exchange
FROM main.assets a
WHERE a.liquidity_rank <= 50
  AND a.first_session <= DATE '{{anchor_date}}'
  AND a.last_session >= DATE '{{anchor_date}}';
CREATE VIEW prediction.price_events AS
SELECT p.price_id, p.asset_id,
  (CAST(p.trading_date AS TIMESTAMP) AT TIME ZONE 'UTC') + INTERVAL 21 HOUR AS observed_at,
  p.adjusted_close, p.volume, p.total_return_1d
FROM main.daily_prices p
JOIN prediction.asset_entities a ON a.asset_id = p.asset_id
WHERE p.trading_date <= DATE '{{anchor_date}}';
CREATE VIEW prediction.return_outcomes AS
WITH sequenced AS (
  SELECT p.*,
    LEAD(p.trading_date, {{horizon_sessions}}) OVER (PARTITION BY p.asset_id ORDER BY p.trading_date) AS realized_date,
    LEAD(p.adjusted_close, {{horizon_sessions}}) OVER (PARTITION BY p.asset_id ORDER BY p.trading_date) AS future_close
  FROM main.daily_prices p
  JOIN prediction.asset_entities a ON a.asset_id = p.asset_id
)
SELECT 'outcome-' || price_id AS outcome_id, asset_id,
  (CAST(trading_date AS TIMESTAMP) AT TIME ZONE 'UTC') + INTERVAL 21 HOUR AS observed_at,
  (CAST(realized_date AS TIMESTAMP) AT TIME ZONE 'UTC') + INTERVAL 21 HOUR AS realized_at,
  future_close / adjusted_close - 1.0 AS forward_return_5d,
  future_close > adjusted_close AS positive_return
FROM sequenced
WHERE realized_date IS NOT NULL AND realized_date <= DATE '{{anchor_date}}';
CREATE VIEW prediction.top_50_population AS
SELECT asset_id FROM prediction.asset_entities ORDER BY asset_id;
