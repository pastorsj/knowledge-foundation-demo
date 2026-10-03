-- peer-network: return correlations of the declared peer pairs (asset_relationships) from 2026-06-01 to
-- 2026-08-31, strongest first. The standard, intraday and large profiles build the graph from these pairs.
WITH window_returns AS (
  SELECT asset_id, trading_date, total_return_1d
  FROM main.daily_prices WHERE trading_date BETWEEN DATE '2026-06-01' AND DATE '2026-08-31'
), pairs AS (
  SELECT DISTINCT least(source_asset_id, target_asset_id) AS first_asset, greatest(source_asset_id, target_asset_id) AS second_asset
  FROM main.asset_relationships
)
SELECT p.first_asset, p.second_asset, corr(x.total_return_1d, y.total_return_1d) AS correlation, count(*) AS sessions
FROM pairs p
JOIN window_returns x ON x.asset_id = p.first_asset
JOIN window_returns y ON y.asset_id = p.second_asset AND y.trading_date = x.trading_date
GROUP BY ALL
ORDER BY correlation DESC, p.first_asset, p.second_asset;
