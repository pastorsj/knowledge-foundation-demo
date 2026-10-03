-- moves-and-filings: the 50 most liquid stocks' returns over February 2026, as market_scan measures them: from the
-- last close before February to the last close in it. The question takes the three strongest and the three
-- weakest; their filings come from retrieval, separately.
WITH closes AS (
  SELECT p.asset_id, a.company_name, a.cik, p.trading_date, p.adjusted_close,
    coalesce(lag(p.adjusted_close) OVER (PARTITION BY p.asset_id ORDER BY p.trading_date), p.adjusted_close)
      AS return_base
  FROM main.daily_prices p JOIN main.assets a USING (asset_id)
  WHERE a.liquidity_rank <= 50
)
SELECT asset_id, company_name, cik,
  arg_max(adjusted_close, trading_date) / arg_min(return_base, trading_date) - 1 AS february_return
FROM closes
WHERE trading_date BETWEEN DATE '2026-02-01' AND DATE '2026-02-28'
GROUP BY asset_id, company_name, cik
ORDER BY february_return DESC, asset_id;
