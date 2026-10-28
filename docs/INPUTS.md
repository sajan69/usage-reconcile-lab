# Input contracts

The importer accepts local files only. It does not download usage or accept API credentials. The contracts below are deliberately narrower than the provider API. All examples are synthetic. Checked against official references on **28 October 2026**.

## Manifest version 1

Each input has a separate JSON manifest. Unknown manifest fields are errors:

```json
{
  "schema_version": 1,
  "source": "app_events_v1",
  "start_utc": "2026-10-01T00:00:00Z",
  "end_utc": "2026-10-03T00:00:00Z",
  "acquired_at": "2026-10-04T00:00:00Z",
  "coverage": "complete",
  "group_by": ["project_id", "model"],
  "batch": false,
  "modalities": ["text"]
}
```

The window is UTC `[start,end)`, spans 1–31 whole UTC days, and ends no later than acquisition. Timestamps require explicit offsets, normalize to UTC and are restricted to years 2000–2100. `coverage` is `complete`, `partial` or `unknown`. Completeness, acquisition time, text-only scope and non-batch filtering are **caller declarations**, not independently authenticated evidence. “Complete” must mean complete for the declared scope, not all organization activity. Never relabel mixed-media/batch data as text-only to make it importable.

## Application JSONL: app_events_v1

One object represents one observed provider attempt, not a stream chunk or a billing unit:

```json
{"event_id":"evt-1","response_id":"resp-1","occurred_at":"2026-10-01T08:00:00Z","project_id":"demo","model":"model-text","usage":{"input_tokens":10,"output_tokens":2,"input_cached_tokens":4}}
```

Application grouping must contain both `project_id` and `model`. Use resolved model identifiers aligned with the provider export, not an assumed equivalence between aliases. Project/model/event/response labels are 1–128 ASCII characters matching `[A-Za-z0-9][A-Za-z0-9_.:/@+-]*`. Use synthetic or opaque aliases instead of personal identifiers. `response_id` may be absent/null. `usage` is required but may be null. Individual token fields absent/null remain unknown; a cached count requires known input and cannot exceed it. Counts must be integers (not booleans/floats), from 0 through 10^12. Cached tokens are already a subset of input; do not add them to input again.

Exact equality of the normalized, allowlisted record for a repeated `event_id` collapses that duplicate. Conflicting normalized content aborts the whole import. Extra application/usage fields are discarded, so differences solely in discarded fields are not a conflict. The quality count describes discarded fields on retained unique records. No raw extras are stored. This projection is not secret scanning: retained labels must also be safe for local storage.

Distinct event IDs are preserved even if they share a response ID; repeated non-null response IDs are counted as an ambiguity. Retry attempts with different responses are not automatically collapsed. A late correction with changed content belongs in a **new observation**, not a second conflicting record within one input. No lifecycle reconstruction or cross-observation merge is performed.

## Provider JSON: openai_completions_v1

Save an ordered set of Completions Usage API response bodies in this local wrapper:

```json
{"pages":[{"object":"page","has_more":false,"next_page":null,"data":[{"object":"bucket","start_time":1790812800,"end_time":1790899200,"results":[{"object":"organization.usage.completions.result","project_id":"demo","model":"model-text","input_tokens":10,"output_tokens":2,"input_cached_tokens":4,"num_model_requests":1}]}]}]}
```

Use the same manifest fields with source `openai_completions_v1`. Only `1d` buckets at UTC midnight are accepted; their Unix-second boundaries must be inside the manifest window. Retain empty buckets, which provide coverage evidence distinct from an omitted day. Buckets must be ordered and non-overlapping; repeated groups within a bucket are refused, never summed silently. Total input/output and request counts are required; cached input may remain unknown.

Manifest `group_by` can contain `project_id`, `model`, both or neither. Grouped dimensions must be non-null; ungrouped dimensions must be absent/null and remain null in storage. **Null here means aggregated**, not a missing project/model to replace with a guessed label. Service-tier, user, key and API-source subgroups are unsupported and must be absent/null. Batch must be absent/null or false, with `batch:false` filtering declared by the manifest. This importer does not establish an endpoint-specific or source-attributed scope from a null `api_source`.

Known nonzero audio/image counters are refused. Absent/null optional modality counters cannot prove text-only usage: the manifest declaration is still necessary. Additional provider fields are not retained. Input totals may include cache writes; optional detail counters are not added to totals. This version does not import Costs API responses, Agents observability schemas, dashboard CSVs or other provider formats.

Intermediate pages must have `has_more:true` and a nonempty cursor. The final page can remain unfinished only with partial/unknown coverage. Repeated cursors are refused. A `complete` declaration also requires every daily bucket and a terminal page. Ordered saved response bodies cannot attest that a collector used each preceding cursor, queried the declared filters or obtained an atomic snapshot. Retain acquisition provenance separately when collecting real data. Hashes identify supplied bytes, not provider authenticity. No live collection is implemented.

## Resource bounds and errors

Each data file is capped at 10 MiB, manifests at 64 KiB, application lines at 64 KiB/20,000 lines, provider bundles at 31 pages/31 buckets/20,000 result rows. Blank JSONL lines are ignored but count toward the line cap. UTF-8/BOM is accepted. Duplicate JSON keys, nonfinite numbers (including overflow) and malformed syntax anywhere abort; invalid values are never skipped. Operational CLI errors contain codes, not raw records/paths. Validated data and canonical context are prepared before opening the database.

## Official references

- [Completions usage reference](https://developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/usage/methods/completions): fields, groupings, bucket bounds, pagination and cached-token semantics.
- [Reviewing API usage and costs](https://help.openai.com/en/articles/10478918-reviewing-api-usage-and-costs): UTC dashboard clock and interrupted Chat Completions streams with missing final usage. Missing final usage is not evidence of zero token consumption.

## Optional reconciliation scope

Comparisons require this optional manifest field on both observations. Old manifests without it still import with unchanged observation IDs, but cannot produce comparisons.

```json
"reconciliation": {
  "organization_id": "demo-org",
  "project_ids": ["demo"],
  "models": ["model-text"],
  "time_basis": "provider_utc_bucket"
}
```

Use one opaque organization identifier, 1–20 distinct project IDs and 1–20 distinct resolved model IDs. Lists normalize in sorted order. Every non-null row dimension must belong to its declared selection; out-of-scope rows abort rather than being dropped. These selections describe the full population being compared, including attempts from other applications in the same projects. They are declarations about the collection/filtering, not proof that it occurred correctly. Batch and text-only restrictions still apply.

`time_basis` is `provider_utc_bucket` or `application_clock`. The former declares that application timestamps have been attributed to the provider's UTC usage buckets; setting the field does **not** perform or verify that attribution. Application wall-clock timestamps can differ from provider accounting time, especially near midnight. Use `application_clock` when attribution is unverified; comparisons will be blocked. Organization, project/model selections, windows, grouping and time basis must match before arithmetic. Null aggregated dimensions are never joined to named dimensions or expanded into guessed records.
