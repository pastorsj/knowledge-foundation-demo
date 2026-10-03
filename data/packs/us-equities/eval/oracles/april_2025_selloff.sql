-- april-2025-follow-up (conversation): the 50 most liquid stocks' returns from 2025-04-01 to 2025-04-08 (turn 1) and
-- from 2025-04-09 to 2025-04-15 (turn 2), each from the close before its window to its last close.
WITH closes AS (
  SELECT p.asset_id, a.company_name, p.trading_date, p.adjusted_close,
    coalesce(lag(p.adjusted_close) OVER (PARTITION BY p.asset_id ORDER BY p.trading_date), p.adjusted_close)
      AS return_base
  FROM main.daily_prices p JOIN main.assets a USING (asset_id)
  WHERE a.liquidity_rank <= 50
)
SELECT asset_id, company_name,
  arg_max(adjusted_close, trading_date) FILTER (WHERE trading_date BETWEEN DATE '2025-04-01' AND DATE '2025-04-08')
    / arg_min(return_base, trading_date) FILTER (WHERE trading_date BETWEEN DATE '2025-04-01' AND DATE '2025-04-08')
    - 1 AS selloff_return,
  arg_max(adjusted_close, trading_date) FILTER (WHERE trading_date BETWEEN DATE '2025-04-09' AND DATE '2025-04-15')
    / arg_min(return_base, trading_date) FILTER (WHERE trading_date BETWEEN DATE '2025-04-09' AND DATE '2025-04-15')
    - 1 AS rebound_return
FROM closes
GROUP BY asset_id, company_name
HAVING selloff_return IS NOT NULL
ORDER BY selloff_return, asset_id;
