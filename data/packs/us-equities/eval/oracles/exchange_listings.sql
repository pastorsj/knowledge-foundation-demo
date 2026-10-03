-- exchange-listings: stocks per listing exchange, in the pack, among the 500 most liquid and among the 50 most liquid.
SELECT exchange,
  count(*) AS stocks,
  count(*) FILTER (WHERE liquidity_rank <= 500) AS among_500_most_liquid,
  count(*) FILTER (WHERE liquidity_rank <= 50) AS among_50_most_liquid
FROM main.assets
GROUP BY exchange
ORDER BY stocks DESC, exchange;
