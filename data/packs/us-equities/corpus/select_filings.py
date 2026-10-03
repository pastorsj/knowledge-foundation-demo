# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Select this pack's SEC filings: 8-Ks by its own issuers over its price window, pinned in a corpus manifest.

    SEC_USER_AGENT="Example Co admin@example.com" uv run --project data \\
        python data/packs/us-equities/corpus/select_filings.py \\
        --assets <a us-equities build>/tables/assets.parquet \\
        --out data/packs/us-equities/corpus/sec-edgar-issuers.manifest.json

The rule: every 8-K filed in the window that reports Item 1.05 (a material cybersecurity incident) by any issuer
in the pack, plus the latest PER_ISSUER 8-Ks in the window of each issuer of the TOP most liquid stocks. Each
issuer's 8-Ks come from its EDGAR submissions, which list the items each 8-K reports. Every selected filing is
downloaded once to pin its SHA-256; `--downloads <data dir>/downloads` keeps them, so `prepare` does not fetch them
again.

This runs rarely, by hand: the manifest it writes is committed and reviewed. The filings remain a document source
for retrieval. They are never turned into news or joined to the price tables; `ticker` is citation metadata.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC
from datetime import date
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb

from demo_data import sec

START, END = date(2025, 1, 2), date(2026, 3, 12)  # the pack's price window
TOP, PER_ISSUER, CAP = 100, 12, 1500
CYBERSECURITY_ITEM = "1.05"
FILING_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}.txt"
PAGE_URL = "https://data.sec.gov/submissions/{name}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--assets", type=Path, required=True, help="tables/assets.parquet of a us-equities build")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--downloads", type=Path, help="a demo-data downloads cache to keep the filings in")
    args = parser.parse_args()

    issuers = liquid_issuers(args.assets)
    pace = sec.Pace(sec.REQUESTS_PER_SECOND)
    print(f"reading the EDGAR submissions of {len(issuers):,} issuers")
    with ThreadPoolExecutor(max_workers=4) as pool:
        found = dict(zip(issuers, pool.map(lambda cik: eight_ks(cik, pace), issuers), strict=True))
    selected = select(issuers, found)
    print(f"selected {len(selected):,} 8-Ks; pinning each one's SHA-256")
    with ThreadPoolExecutor(max_workers=4) as pool:
        filings = list(pool.map(lambda filing: pin(filing, pace, args.downloads), selected))
    args.out.write_text(json.dumps(manifest(filings), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.out}: {len(filings):,} filings by {len({f['filing_id'][:10] for f in filings}):,} issuers")


def liquid_issuers(assets: Path) -> dict[int, tuple[str, int]]:
    """CIK -> (ticker, liquidity rank) of its most liquid share class, most liquid issuer first."""
    rows = duckdb.execute(
        "SELECT CAST(cik AS BIGINT), asset_id, liquidity_rank FROM read_parquet(?) WHERE cik IS NOT NULL "
        "ORDER BY liquidity_rank",
        [str(assets)],
    ).fetchall()
    issuers: dict[int, tuple[str, int]] = {}
    for cik, ticker, rank in rows:
        issuers.setdefault(cik, (ticker, rank))
    return issuers


def eight_ks(cik: int, pace: sec.Pace) -> list[dict[str, Any]]:
    """The issuer's 8-Ks filed in the window, newest first, from its submissions (older pages when needed)."""
    pace.wait()
    submissions = json.loads(sec.get(sec.SUBMISSIONS_URL.format(cik=cik)))
    pages = [submissions["filings"]["recent"]]
    for page in submissions["filings"].get("files", []):
        if date.fromisoformat(page["filingTo"]) >= START:
            pace.wait()
            pages.append(json.loads(sec.get(PAGE_URL.format(name=page["name"]))))
    found = []
    for page in pages:
        for accession, filed, form, items in zip(
            page["accessionNumber"], page["filingDate"], page["form"], page["items"], strict=True
        ):
            if form == "8-K" and START <= date.fromisoformat(filed) <= END:
                found.append(
                    {
                        "cik": cik,
                        "company_name": submissions["name"],
                        "accession": accession,
                        "filed_on": filed,
                        "items": items,
                    }
                )
    return sorted(found, key=lambda filing: filing["filed_on"], reverse=True)


def select(issuers: dict[int, tuple[str, int]], found: dict[int, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Every Item 1.05 8-K, then the latest PER_ISSUER 8-Ks of the TOP stocks' issuers, most liquid first, up to CAP."""
    cyber = [f for filings in found.values() for f in filings if CYBERSECURITY_ITEM in f["items"].split(",")]
    latest = [f for cik, (_, rank) in issuers.items() if rank <= TOP for f in found[cik][:PER_ISSUER]]
    chosen: dict[str, dict[str, Any]] = {}
    for filing in cyber + latest:
        if len(chosen) < CAP:
            chosen.setdefault(filing["accession"], filing | {"ticker": issuers[filing["cik"]][0]})
    return sorted(chosen.values(), key=lambda filing: (filing["filed_on"], filing["cik"], filing["accession"]))


def pin(filing: dict[str, Any], pace: sec.Pace, downloads: Path | None) -> dict[str, Any]:
    """The manifest entry, with the SHA-256 of the full submission as downloaded now."""
    url = FILING_URL.format(cik=filing["cik"], accession=filing["accession"])
    pace.wait()
    body = sec.get(url)
    digest = hashlib.sha256(body).hexdigest()
    if downloads is not None:
        (downloads / "sha256").mkdir(parents=True, exist_ok=True)
        (downloads / "sha256" / digest).write_bytes(body)
    return {
        "filing_id": f"{filing['cik']:010d}:{filing['accession']}",
        "company_name": filing["company_name"],
        "ticker": filing["ticker"],
        "form": "8-K",
        "filed_on": filing["filed_on"],
        "items": filing["items"],
        "source_url": url,
        "sha256": digest,
    }


def manifest(filings: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "source_id": "sec_filings",
        "snapshot_id": f"us-equities-8k-{START}-{END}-v1",
        "as_of": str(END),
        "selection": {
            "source": "each issuer's EDGAR submissions (data.sec.gov), read " + datetime.now(UTC).date().isoformat(),
            "forms": ["8-K"],
            "window": [str(START), str(END)],
            "rule": f"every 8-K reporting Item {CYBERSECURITY_ITEM} by any issuer in the pack, plus the latest "
            f"{PER_ISSUER} 8-Ks of each issuer of the {TOP} most liquid stocks; at most {CAP:,} filings",
            "script": "corpus/select_filings.py",
            "documents": "the primary document and EX-99 exhibits",
        },
        "transform_version": "edgar-disclosure-chunker-v1",
        "filings": filings,
    }


if __name__ == "__main__":
    main()
