-- news-and-filings (market half): negative company news about the 12 most liquid issuers in July and August
-- 2026, per issuer, with the issuer's return over those two months (from the last close before July, as
-- price_context measures it).
WITH news AS (
  SELECT primary_asset_id AS asset_id, count(*) FILTER (WHERE sentiment_label = 'negative') AS negative_items,
    count(*) AS items
  FROM main.company_news
  WHERE published_at >= TIMESTAMPTZ '2026-07-01 00:00:00+00' AND published_at < TIMESTAMPTZ '2026-09-01 00:00:00+00'
  GROUP BY 1
), closes AS (
  SELECT asset_id, trading_date, adjusted_close,
    coalesce(lag(adjusted_close) OVER (PARTITION BY asset_id ORDER BY trading_date), adjusted_close) AS return_base
  FROM main.daily_prices
), window_prices AS (
  SELECT asset_id, arg_min(return_base, trading_date) AS base_close, arg_max(adjusted_close, trading_date) AS last_close
  FROM closes WHERE trading_date BETWEEN DATE '2026-07-01' AND DATE '2026-08-31' GROUP BY 1
)
SELECT a.asset_id, a.company_name, coalesce(n.negative_items, 0) AS negative_items, coalesce(n.items, 0) AS items,
  w.last_close / w.base_close - 1 AS total_return
FROM main.assets a JOIN window_prices w USING (asset_id) LEFT JOIN news n USING (asset_id)
WHERE a.liquidity_rank <= 12
ORDER BY negative_items DESC, a.asset_id;
