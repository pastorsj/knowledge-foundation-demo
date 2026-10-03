-- large-universe-scan: the 500 most liquid stocks' returns from 2026-01-02 to 2026-03-12 (from the close before
-- the window, as market_scan measures them) and how unusual their trading volume was: the z-score of each stock's
-- total volume in the window among the 500 (population standard deviation), as market_scan reports it in `zscores`.
WITH closes AS (
  SELECT p.asset_id, p.trading_date, p.adjusted_close, p.volume,
    coalesce(lag(p.adjusted_close) OVER (PARTITION BY p.asset_id ORDER BY p.trading_date), p.adjusted_close)
      AS return_base
  FROM main.daily_prices p JOIN main.assets a USING (asset_id)
  WHERE a.liquidity_rank <= 500
),
per_asset AS (
  SELECT asset_id,
    arg_max(adjusted_close, trading_date) / arg_min(return_base, trading_date) - 1 AS total_return,
    sum(volume) AS total_volume
  FROM closes WHERE trading_date BETWEEN DATE '2026-01-02' AND DATE '2026-03-12'
  GROUP BY asset_id
)
SELECT asset_id, total_return, total_volume,
  (total_volume - avg(total_volume) OVER ()) / stddev_pop(total_volume) OVER () AS volume_zscore
FROM per_asset
ORDER BY total_return DESC, asset_id;
