-- Cost per run, computed from stored telemetry joined to IceGate's price table.
-- Local Ollama models have no price row, so cost_usd is NULL for them — that is
-- the honest result, and the LEFT JOIN makes it visible rather than dropping the
-- row entirely, which is what an inner join would do.
--
-- ASSUMPTION: at most one active price row per (model, provider). The `prices`
-- schema carries valid_from / service_tier / region / min_input_tokens /
-- max_input_tokens precisely to support several rows per model, and if more than
-- one matches, this join fans out BEFORE aggregation -- inflating sum(input_tokens)
-- and sum(output_tokens) too, not just the price factor. That is silent wrong
-- numbers, not an error. Once `prices` holds multiple rows per model, pick one
-- first (e.g. ROW_NUMBER() OVER (PARTITION BY model, provider ORDER BY valid_from
-- DESC)) and join to that.
SELECT o.request_model,
       sum(o.input_tokens)                                   AS input_tokens,
       sum(o.output_tokens)                                  AS output_tokens,
       sum(o.input_tokens)  / 1e6 * max(p.input_usd_per_1m)  AS input_cost_usd,
       sum(o.output_tokens) / 1e6 * max(p.output_usd_per_1m) AS output_cost_usd,
       sum(o.input_tokens)  / 1e6 * max(p.input_usd_per_1m)
     + sum(o.output_tokens) / 1e6 * max(p.output_usd_per_1m) AS total_cost_usd,
       max(p.currency)                                       AS currency
FROM iceberg.icegate.operations o
JOIN iceberg.icegate.spans s
  ON o.span_id = s.span_id
LEFT JOIN iceberg.icegate.prices p
  ON p.model = o.request_model
 AND p.provider = o.provider_name
WHERE array_element(map_extract(s.resource_attributes, 'icegate.run_id'), 1) = $1
  AND o.operation_name = 'chat'
GROUP BY o.request_model;
