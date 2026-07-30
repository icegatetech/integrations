-- Token usage per model for one run.
-- Reads `operations`, not `spans`: span_attributes is Map<String,String>, so
-- token counts there are strings. `operations` has real integers.
SELECT o.request_model,
       count(*)              AS calls,
       sum(o.input_tokens)   AS input_tokens,
       sum(o.output_tokens)  AS output_tokens,
       sum(o.total_tokens)   AS total_tokens
FROM iceberg.icegate.operations o
JOIN iceberg.icegate.spans s ON o.span_id = s.span_id
WHERE array_element(map_extract(s.resource_attributes, 'icegate.run_id'), 1) = $1
  AND o.operation_name = 'chat'
GROUP BY o.request_model
ORDER BY total_tokens DESC;
