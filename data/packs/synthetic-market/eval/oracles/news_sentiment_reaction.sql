-- news-sentiment-reaction: company news about the 12 most liquid issuers published 2026-08-17 to 2026-08-24,
-- aligned like analyze_news_price_relationship: the first session closing (21:00 UTC) at or after publication,
-- then the return from that close to the close five sessions later. The window ends on 2026-08-24 because the
-- data ends five sessions later, on 2026-08-31, so every item has its full five sessions.
WITH sessions AS (
  SELECT p.asset_id, p.trading_date, p.adjusted_close,
    (CAST(p.trading_date AS TIMESTAMP) AT TIME ZONE 'UTC') + INTERVAL 21 HOUR AS closes_at,
    lead(p.adjusted_close, 5) OVER (PARTITION BY p.asset_id ORDER BY p.trading_date) AS close_five_later
  FROM main.daily_prices p
), items AS (
  SELECT n.news_id, n.primary_asset_id AS asset_id, n.published_at, n.sentiment_label, n.is_story,
    (SELECT min(s.trading_date) FROM sessions s
     WHERE s.asset_id = n.primary_asset_id AND s.closes_at >= n.published_at) AS aligned_session
  FROM main.company_news n JOIN main.assets a ON a.asset_id = n.primary_asset_id
  WHERE a.liquidity_rank <= 12
    AND n.published_at >= TIMESTAMPTZ '2026-08-17 00:00:00+00' AND n.published_at < TIMESTAMPTZ '2026-08-25 00:00:00+00'
)
SELECT i.sentiment_label, count(*) AS items, count(*) FILTER (WHERE i.is_story) AS stories,
  avg(s.close_five_later / s.adjusted_close - 1) AS mean_forward_return_5
FROM items i JOIN sessions s ON s.asset_id = i.asset_id AND s.trading_date = i.aligned_session
WHERE s.close_five_later IS NOT NULL
GROUP BY i.sentiment_label
ORDER BY i.sentiment_label;
