# Daily comparison semantics

`usage-reconcile compare --db DB --app APP_SNAPSHOT --provider PROVIDER_SNAPSHOT` produces a bounded JSON report. Snapshot IDs are validated and bound as query parameters. A read-only transaction reads both observations and their rows consistently; no database writes or source downloads occur. The packaged [daily SQL](../src/usage_reconcile/sql/daily.sql) is inspectable.

Comparisons require matching UTC windows, organization/project/model selections, grouping and a declared provider-bucket time basis. Old observations without reconciliation metadata remain readable but return a blocked report with no comparison rows. Aggregated/null provider dimensions and named application dimensions cannot be compared. No automatic model alias mapping or attribution correction occurs. The report includes both manifests, hashes, quality counts, blockers and warnings. Acquisitions can differ in time; late data can change the provider's observation without proving a collection or billing fault.

For a compatible pair, the report enumerates declared UTC day × project × model combinations, capped at 2,000 groups. It shows observed application-event counts, known token subtotals, unknown-count denominators, response-identity ambiguities and provider totals. This grid does **not** manufacture data: an absent group remains unobserved even within an explicitly present empty bucket. There is no zero-fill for either side. A provider bucket's presence is reported separately from a group's presence.

Four metrics are evaluated independently: request count, total input, total output and cached input. The signed difference is **provider minus application**. A non-null difference means only a conditional comparison under the supplied declarations, never a verified charge discrepancy. Both sources must declare complete coverage, have an observed group, and have no missing or repeated application response identity affecting that group. Repeated identities across days/projects/models affect each involved group; the tool preserves the records rather than deciding which one to discard. Distinct retry responses remain separate attempts.

Unknown application counts suppress that metric's complete total and difference. Known subtotals remain available in `observations`, with their unknown-count denominators. A known request count can still be compared when token usage is unknown. Unknown cached input suppresses only the cached metric when other conditions hold. Cached input is already part of total input and is never added again. Explicit zero token counts are valid observations and can yield a zero difference.

Incomplete/unknown coverage suppresses all differences, including for present groups. Missing buckets, groups or response identity produce explicit reason codes. Blocked pairs are successful report generation with `status: blocked`, not CLI operational failures. Consumers must inspect blockers and per-metric status; exit code zero does not mean a population was reconciled. Operational errors (missing snapshots, wrong source type, unsafe database or size limits) return an error code and exit 2.

These rules are conservative. They cannot establish whether telemetry represents billable model attempts, whether saved exports match their declared filters, or whether an application timestamp truly matches provider attribution. No provider credentials, costs, prices, invoice matching, cross-observation merges or production accuracy claims are involved.

## Local report files

```sh
usage-reconcile export --db DB --app APP_SNAPSHOT --provider PROVIDER_SNAPSHOT --format json --out NEW_REPORT.json
usage-reconcile export --db DB --app APP_SNAPSHOT --provider PROVIDER_SNAPSHOT --format csv --out NEW_REPORT.csv
usage-reconcile export --db DB --app APP_SNAPSHOT --provider PROVIDER_SNAPSHOT --format html --out NEW_REPORT.html
```

All formats are capped at 2 MiB after full serialization and refuse existing outputs/symlinks using exclusive creation. New POSIX files use 0600; choose a trusted output directory. Physical write failures can leave a partial file. No overwrite/retry or directory cleanup is attempted.

JSON preserves the complete aggregate report, provenance and nulls. CSV has one row per metric/group, repeats context/warnings and uses an empty cell for unknown values with a status/reason. A blocked report has one explanation row. Known subtotals and unknown counts are separate columns. All CSV cells are quoted; potentially formula-like text, including whitespace/fullwidth forms, is prefixed with an apostrophe. This reduces common spreadsheet formula risks but is not a guarantee for every spreadsheet importer. Numeric signed differences remain numbers.

HTML is a standalone, script-free report. Open it locally in a browser; no server, API, remote assets or credentials are used. Text, including provenance JSON, is HTML-escaped. A restrictive meta CSP disallows scripts/network requests and permits only the fixed inline stylesheet. Expandable details expose subtotals/unknown counts and provenance; horizontally scrollable tables support narrow screens. It has no editing, collection or download endpoints. Sharing any report also shares its retained organization/project/model labels and provenance; review these first.

## Reproduced synthetic findings

Run `usage-reconcile demo-comparison --out NEW_DIR`. It constructs temporary, explicitly synthetic inputs, imports both observations, compares them, checks independent expected outcomes and exports all three formats. There are nine unique application events (ten input lines including one projected duplicate), five provider groups, two UTC days and three model labels. The generated model names and token values do not refer to production usage. The [recorded JSON example](demo-comparison.json) contains the aggregate output and provenance.

| Case | Actual result |
| --- | --- |
| Day 1, synthetic-text-model | Three application events; one has unknown usage. Known input subtotal is 10, complete input total/difference withheld; request difference is 0. |
| Day 2, synthetic-text-model | Repeated response identity affects the group; all four metric differences withheld. Records remain visible as observations. |
| Day 1, matched-text-model | All four metric differences are 0. |
| Day 2, matched-text-model | Application input is 25, provider input is 27; conditional difference is +2. Other metric differences are 0. No billing explanation is inferred. |
| Day 1, zero-text-model | Explicitly recorded zero token totals compare as 0, with one request on each side. |
| Day 2, zero-text-model | Both groups are unobserved despite the daily provider bucket being present; differences remain unknown, not 0. |

Together these yield six groups, 13 conditional metric comparisons and 11 withheld values. Fifteen additional comparison test methods exercise scope/window/aggregate mismatches, partial captures, missing identities, unknown cache counts, distinct retry responses, late corrections, read-only behavior and the group cap. This is synthetic functional evidence, not detection accuracy, representative usage or real invoice validation.

Browser checks open the generated standalone HTML in Chromium: worked totals/unknowns, expandable provenance, a 390 px layout with horizontally scrollable tables, and an adversarial internal-label probe that stays inert with no network requests. Screenshots and check results are CI artifacts rather than committed application data. Parser-level HTML/CSV injection and exclusive-output tests run in the Python suite as well.
