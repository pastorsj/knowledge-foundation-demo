-- volatility-ranking: the 50 most liquid stocks over the 60 sessions ending 2026-03-12, as market_scan measures
-- them: the standard deviation of the 60 daily returns, and the return from the close before the window to its
-- last close.
WITH ranked AS (
  SELECT p.asset_id, a.company_name, p.adjusted_close, p.total_return_1d,
    coalesce(lag(p.adjusted_close) OVER (PARTITION BY p.asset_id ORDER BY p.trading_date), p.adjusted_close)
      AS return_base,
    row_number() OVER (PARTITION BY p.asset_id ORDER BY p.trading_date DESC) AS recency
  FROM main.daily_prices p JOIN main.assets a USING (asset_id)
  WHERE a.liquidity_rank <= 50 AND p.trading_date <= DATE '2026-03-12'
)
SELECT asset_id, company_name,
  stddev_samp(total_return_1d) AS daily_volatility,
  max(adjusted_close) FILTER (WHERE recency = 1) / max(return_base) FILTER (WHERE recency = 60) - 1 AS total_return
FROM ranked WHERE recency <= 60
GROUP BY asset_id, company_name
ORDER BY daily_volatility DESC, asset_id;
