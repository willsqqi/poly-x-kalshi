SELECT COUNT(*) AS null_market_id_rows
FROM prediction_market_dev.fact_trades
WHERE market_id IS NULL OR market_id = '';

SELECT trade_id, COUNT(*) AS duplicate_count
FROM prediction_market_dev.fact_trades
GROUP BY trade_id
HAVING COUNT(*) > 1
ORDER BY duplicate_count DESC
LIMIT 50;

SELECT date, COUNT(*) AS rows, SUM(usd_amount) AS usd_volume
FROM prediction_market_dev.fact_trades
GROUP BY date
ORDER BY date;

SELECT COUNT(*) AS unmatched_outcomes
FROM prediction_market_dev.fact_trades t
LEFT JOIN prediction_market_dev.dim_outcome o
  ON t.outcome_id = o.outcome_id
WHERE o.outcome_id IS NULL;
