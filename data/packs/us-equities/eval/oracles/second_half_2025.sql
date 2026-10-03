-- second-half-2025: the 500 most liquid stocks' returns from 2025-07-01 to 2025-12-31, from the close before
-- the window to its last close, and the standard deviation of their daily returns in it.
WITH closes AS (
  SELECT p.asset_id, a.company_name, p.trading_date, p.adjusted_close, p.total_return_1d,
    coalesce(lag(p.adjusted_close) OVER (PARTITION BY p.asset_id ORDER BY p.trading_date), p.adjusted_close)
      AS return_base
  FROM main.daily_prices p JOIN main.assets a USING (asset_id)
  WHERE a.liquidity_rank <= 500
)
SELECT asset_id, company_name,
  arg_max(adjusted_close, trading_date) / arg_min(return_base, trading_date) - 1 AS total_return,
  stddev_samp(total_return_1d) AS daily_volatility
FROM closes WHERE trading_date BETWEEN DATE '2025-07-01' AND DATE '2025-12-31'
GROUP BY asset_id, company_name
ORDER BY total_return DESC, asset_id;
