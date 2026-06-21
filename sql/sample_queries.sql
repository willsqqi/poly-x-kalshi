SELECT COUNT(*) AS fact_trade_count
FROM prediction_market_dev.fact_trades;

SELECT market_id, SUM(usd_amount) AS volume
FROM prediction_market_dev.fact_trades
GROUP BY market_id
ORDER BY volume DESC
LIMIT 20;

SELECT date, SUM(daily_volume) AS volume
FROM prediction_market_dev.fact_market_daily
GROUP BY date
ORDER BY date;

SELECT m.question, d.date, d.daily_volume, d.daily_trade_count, d.close_price
FROM prediction_market_dev.fact_market_daily d
JOIN prediction_market_dev.dim_market m
  ON d.market_id = m.market_id
ORDER BY d.daily_volume DESC
LIMIT 20;
