-- intraday-ranges (the 1-minute profiles): the widest regular-session ranges (high over low, minus 1) of the 12
-- most liquid issuers from 2026-08-17 to 2026-08-28. The daily bars are the minute bars' rollup, so their high,
-- low, open and close are intraday_scan's; its volume shares need the minute bars themselves.
SELECT p.asset_id, a.company_name, p.trading_date, p.high / p.low - 1 AS intraday_range,
  p.close / p.open - 1 AS open_to_close_return, p.bar_count
FROM main.daily_prices p JOIN main.assets a USING (asset_id)
WHERE a.liquidity_rank <= 12 AND p.trading_date BETWEEN DATE '2026-08-17' AND DATE '2026-08-28'
ORDER BY intraday_range DESC, p.asset_id, p.trading_date;
