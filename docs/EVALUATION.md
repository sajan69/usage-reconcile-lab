# Evaluation and measurement method

The examples are synthetic, local and credential-free. They validate specified behavior rather than measuring real-provider accuracy. The [worked comparison](COMPARISONS.md#reproduced-synthetic-findings) explains six groups and why 13 metrics are conditionally comparable while 11 are withheld. The Python suite also exercises malformed inputs, scope/coverage mismatches, unknowns, duplicates, rollback and output security. Chromium checks validate the standalone report, not a hosted service or every browser.

## Reproduce a bounded workload

```sh
usage-reconcile benchmark --rows 800 --repeats 3
```

Rows must be multiples of 80 from 80 through 8,000; repetitions range from 1 through 5. Bounds are checked before fixture allocation or file creation. A private temporary directory contains generated files and a separate fresh database per repetition; no supplied database or user file is changed. The JSON output contains every timed sample, medians, input file sizes/hashes, dataset hash, environment and method.

The deterministic workload spans two UTC days, two projects and two model labels: eight groups. Each application event has a unique event/response identity; one in ten omits usage, and one in twenty is replayed with the same event ID. Provider totals contain the complete underlying synthetic counts. A fully observed group has an intentional +3 input-token difference. For 800 unique events this gives 840 input lines, 40 exact duplicates, 80 unknown-usage events and eight provider result groups. Four groups have unknown token totals: request differences remain eligible, but three token differences per group are withheld. Expected output is 20 conditional metrics and 12 withheld metrics.

After each timed repetition, the benchmark verifies imported/duplicate/unknown counts, provider rows, zero inserted on reimport, all group sizes, statuses and every expected difference. A mismatch fails rather than recording a successful measurement. The benchmark does not execute or collect real provider traffic.

Each repetition measures, in order:

1. Application import into a fresh database: file reads, manifest/JSON parsing, validation, deduplication, schema creation, provenance, transaction, database open/close.
2. Provider import into that same database: corresponding parsing/validation/provenance/transaction work, with the schema already present.
3. Application reimport: full validation and identity lookup, with no new rows.
4. Comparison: read-only metadata/SQL queries, context checks and report-object assembly, including database open/close.

Timers use `time.perf_counter` wall-clock milliseconds. Fixture generation/writes, correctness assertions, report JSON/CSV/HTML serialization, browser rendering, CLI process startup and network/TLS are excluded. There is no explicit warmup or cache clearing. Repeated inputs may benefit from OS caches even though databases are fresh. Medians summarize the individual samples for each operation; there is no baseline or claimed improvement. Shared-host hardware and an uncharacterized temporary filesystem limit portability. This is not a concurrency, production throughput, invoice or capacity benchmark.

## Design and practical limits

The data path is strict local parsing → normalized immutable observations → read-only SQL → reasoned metrics → bounded exports. Keeping raw extras out of storage reduces accidental retention; it does not sanitize meaningful labels or encrypt the database. Python validates scope and types before DuckDB writes, while a transaction keeps provenance and rows together. SQL computes observed counts/subtotals, and application code withholds unsafe arithmetic according to explicit comparability rules.

Caller declarations cannot authenticate export origin, population completeness or provider timestamp attribution. The supported provider adapter is deliberately narrow: daily text/non-batch completion-usage buckets grouped by project and model for comparisons. Null aggregated dimensions remain readable but do not match named groups. No live provider integration, costs/prices, invoice certification, secrets scanning, cross-observation correction merge, encrypted storage, hostile-database sandbox or physical power-loss certification is claimed. See [input contracts](INPUTS.md) and [storage assumptions](STORAGE.md).
