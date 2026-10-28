-- UTC attribution comes from the declared occurred_at time basis, not inferred
-- from export acquisition time. Missing token values never become complete sums.
WITH events AS (
    SELECT *, count(*) OVER (PARTITION BY response_id) AS response_occurrences
    FROM app_events WHERE snapshot_id = $app
), app AS (
    SELECT CAST(date_trunc('day', occurred_at) AS DATE)::VARCHAR AS day,
           project_id, model, count(*) AS app_events,
           count(*) FILTER (WHERE response_id IS NULL) AS unidentified_responses,
           count(*) FILTER (WHERE response_id IS NOT NULL AND response_occurrences > 1) AS ambiguous_responses,
           sum(input_tokens) AS app_known_input,
           sum(output_tokens) AS app_known_output,
           sum(input_cached_tokens) AS app_known_cached,
           count(*) - count(input_tokens) AS unknown_input,
           count(*) - count(output_tokens) AS unknown_output,
           count(*) - count(input_cached_tokens) AS unknown_cached
    FROM events GROUP BY day, project_id, model
), provider AS (
    SELECT CAST(to_timestamp(start_time) AS DATE)::VARCHAR AS day, project_id, model,
           num_model_requests AS provider_requests, input_tokens AS provider_input,
           output_tokens AS provider_output, input_cached_tokens AS provider_cached
    FROM provider_usage WHERE snapshot_id = $provider
)
SELECT coalesce(a.day,p.day) AS day, coalesce(a.project_id,p.project_id) AS project_id,
       coalesce(a.model,p.model) AS model,
       a.app_events, a.unidentified_responses, a.ambiguous_responses,
       a.app_known_input, a.app_known_output, a.app_known_cached,
       a.unknown_input, a.unknown_output, a.unknown_cached,
       p.provider_requests, p.provider_input, p.provider_output, p.provider_cached
FROM app a FULL OUTER JOIN provider p USING(day, project_id, model)
ORDER BY day, project_id, model
LIMIT 2001
