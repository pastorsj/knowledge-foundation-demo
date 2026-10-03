-- sector-sql: stocks per SIC division and the division's median return from 2026-01-02 to 2026-03-12, each return
-- from the close before the window to the last close in it.
WITH closes AS (
  SELECT asset_id, trading_date, adjusted_close,
    coalesce(lag(adjusted_close) OVER (PARTITION BY asset_id ORDER BY trading_date), adjusted_close) AS return_base
  FROM main.daily_prices
), returns AS (
  SELECT asset_id,
    arg_max(adjusted_close, trading_date) / arg_min(return_base, trading_date) - 1 AS total_return
  FROM closes WHERE trading_date BETWEEN DATE '2026-01-02' AND DATE '2026-03-12'
  GROUP BY asset_id
)
SELECT a.sector, count(*) AS stocks, median(r.total_return) AS median_return
FROM main.assets a LEFT JOIN returns r USING (asset_id)
GROUP BY a.sector
ORDER BY stocks DESC, a.sector;
