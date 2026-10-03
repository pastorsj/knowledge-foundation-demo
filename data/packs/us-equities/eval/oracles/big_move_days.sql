-- big-move-days: among the 500 most liquid stocks, the number of 2025 sessions whose close moved more than 10% from
-- the previous close, in either direction, with the largest rise and fall.
SELECT p.asset_id, a.company_name,
  count(*) FILTER (WHERE abs(p.total_return_1d) > 0.10) AS big_move_sessions,
  max(p.total_return_1d) AS largest_rise,
  min(p.total_return_1d) AS largest_fall
FROM main.daily_prices p JOIN main.assets a USING (asset_id)
WHERE a.liquidity_rank <= 500 AND p.trading_date BETWEEN DATE '2025-01-02' AND DATE '2025-12-31'
GROUP BY p.asset_id, a.company_name
ORDER BY big_move_sessions DESC, p.asset_id;
