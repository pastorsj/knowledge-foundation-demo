-- large-universe-scan: every issuer's return from 2024-01-02 to 2026-08-31, as market_scan measures it: from the
-- close before its first session in the window (its first close, with no earlier session) to its last on or
-- before 2026-08-31; the question asks for the 20 strongest and the 20 weakest.
WITH closes AS (
  SELECT asset_id, trading_date, adjusted_close, total_return_1d,
    coalesce(lag(adjusted_close) OVER (PARTITION BY asset_id ORDER BY trading_date), adjusted_close) AS return_base
  FROM main.daily_prices
), window_prices AS (
  SELECT asset_id, arg_min(return_base, trading_date) AS base_close,
    arg_max(adjusted_close, trading_date) AS last_close, stddev_samp(total_return_1d) AS daily_volatility
  FROM closes WHERE trading_date BETWEEN DATE '2024-01-02' AND DATE '2026-08-31' GROUP BY 1
)
SELECT asset_id, last_close / base_close - 1 AS total_return, daily_volatility
FROM window_prices
ORDER BY total_return DESC, asset_id;
