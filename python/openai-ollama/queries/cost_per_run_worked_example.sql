-- Worked example: same arithmetic as cost_per_run.sql, but with prices supplied
-- inline instead of joined from `prices`.
--
-- Use this when `prices` is empty (IceGate's pricing crawler is off by default).
-- The rates below are illustrative stand-ins for a paid model, so the numbers
-- show the shape of the calculation, not a real bill. Local Ollama models cost
-- nothing to run; the point is that the arithmetic works off stored telemetry.
WITH rates AS (
    SELECT 'gemma4:12b-mlx' AS model,
           0.15             AS input_usd_per_1m,
           0.60             AS output_usd_per_1m
)
SELECT o.request_model,
       sum(o.input_tokens)                                AS input_tokens,
       sum(o.output_tokens)                               AS output_tokens,
       sum(o.input_tokens)  / 1e6 * max(r.input_usd_per_1m)  AS input_cost_usd,
       sum(o.output_tokens) / 1e6 * max(r.output_usd_per_1m) AS output_cost_usd,
       sum(o.input_tokens)  / 1e6 * max(r.input_usd_per_1m)
     + sum(o.output_tokens) / 1e6 * max(r.output_usd_per_1m) AS total_cost_usd
FROM iceberg.icegate.operations o
JOIN iceberg.icegate.spans s ON o.span_id = s.span_id
JOIN rates r ON r.model = o.request_model
WHERE array_element(map_extract(s.resource_attributes, 'icegate.run_id'), 1) = $1
  AND o.operation_name = 'chat'
GROUP BY o.request_model;
