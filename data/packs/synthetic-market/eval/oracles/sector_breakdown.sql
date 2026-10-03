-- sector-sql: issuers per sector and the median return over the 20 sessions ending 2026-08-31, each from the close
-- before the window to its last close (20 daily returns).
WITH ranked AS (
  SELECT asset_id, adjusted_close,
    coalesce(lag(adjusted_close) OVER (PARTITION BY asset_id ORDER BY trading_date), adjusted_close) AS return_base,
    row_number() OVER (PARTITION BY asset_id ORDER BY trading_date DESC) AS recency
  FROM main.daily_prices WHERE trading_date <= DATE '2026-08-31'
), returns AS (
  SELECT asset_id,
    max(adjusted_close) FILTER (WHERE recency = 1) / max(return_base) FILTER (WHERE recency = 20) - 1 AS total_return
  FROM ranked WHERE recency <= 20 GROUP BY asset_id
)
SELECT a.sector, count(*) AS issuers, median(r.total_return) AS median_return
FROM main.assets a JOIN returns r USING (asset_id)
GROUP BY a.sector
ORDER BY issuers DESC, a.sector;
