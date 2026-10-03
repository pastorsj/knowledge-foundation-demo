-- peer-network: daily-return correlation of each declared peer pair over the graph window (2025-12-01 to
-- 2026-03-12), as analyze_market_relationships computes it in sparse_declared_peers mode, for pairs that traded
-- together on at least 20 sessions. Share classes of one company (GOOG and GOOGL) are peers too.
WITH pairs AS (
  SELECT DISTINCT least(source_asset_id, target_asset_id) AS first_asset,
    greatest(source_asset_id, target_asset_id) AS second_asset
  FROM main.asset_relationships
),
returns AS (
  SELECT asset_id, trading_date, total_return_1d FROM main.daily_prices
  WHERE trading_date BETWEEN DATE '2025-12-01' AND DATE '2026-03-12'
)
SELECT pairs.first_asset, pairs.second_asset, corr(x.total_return_1d, y.total_return_1d) AS correlation,
  count(*) AS sessions
FROM pairs
JOIN returns x ON x.asset_id = pairs.first_asset
JOIN returns y ON y.asset_id = pairs.second_asset AND y.trading_date = x.trading_date
GROUP BY ALL
HAVING count(*) >= 20
ORDER BY correlation DESC, pairs.first_asset, pairs.second_asset;
