-- story-event-context: every company news item about the 12 most liquid issuers published from 2026-08-17 through
-- 2026-08-28 (the 12 planted stories, is_story, among them), aligned as analyze_news_price_relationship aligns it: to
-- the issuer's first session closing at or after publication (sessions close at 21:00 UTC). With that session's
-- return and the return over the two sessions after it, which is null when the data ends first: it ends on
-- 2026-08-31, so the items of 2026-08-28 have only their publication session's return.
WITH sessions AS (
  SELECT asset_id, trading_date, adjusted_close, total_return_1d,
    timezone('UTC', CAST(trading_date AS TIMESTAMP) + INTERVAL 21 HOUR) AS closes_at,
    lead(adjusted_close, 2) OVER (PARTITION BY asset_id ORDER BY trading_date) AS close_two_later
  FROM main.daily_prices
), news AS (
  SELECT n.news_id, n.primary_asset_id AS asset_id, n.published_at, n.is_story, n.event_type, n.sentiment_label,
    n.headline
  FROM main.company_news n JOIN main.assets a ON a.asset_id = n.primary_asset_id
  WHERE a.liquidity_rank <= 12
    AND n.published_at BETWEEN TIMESTAMPTZ '2026-08-17 00:00:00+00' AND TIMESTAMPTZ '2026-08-28 23:59:59+00'
)
SELECT n.news_id, n.asset_id, n.published_at, n.is_story, n.event_type, n.sentiment_label, n.headline,
  s.trading_date AS publication_session, s.total_return_1d AS publication_session_return,
  s.close_two_later / s.adjusted_close - 1 AS following_two_session_return
FROM news n ASOF LEFT JOIN sessions s ON s.asset_id = n.asset_id AND s.closes_at >= n.published_at
ORDER BY n.published_at, n.news_id;
