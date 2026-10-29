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

## Recorded workload sample

The [machine-readable record](benchmark.json) was produced by [CI run 40797307783](https://github.com/sajan69/usage-reconcile-lab/actions/runs/40797307783) at source commit [836124ba](https://github.com/sajan69/usage-reconcile-lab/commit/836124ba4f29404f4dd74045baf31bb5e6a91284), at **2026-10-29 00:56:57 UTC**. This is a recorded sample, not a performance promise; subsequent CI runs produce new measurements.

Workload: 800 unique application events, 840 input lines, 40 exact duplicates, 80 unknown-usage events, eight provider result groups and three repetitions. Every repetition verified 20 conditional metrics, 12 withheld metrics, the intentional +3 input difference, all other eligible zero differences and no rows inserted on reimport. Application input is 177,167 bytes; provider input is 1,797 bytes. Combined dataset SHA-256: `0e699f841022134aad460d22f258b44677099a350ccbd629194830cc26cda1b3`. All four input/manifest hashes and every individual timing are in the JSON record.

| Operation | Median wall-clock ms, three repetitions |
| --- | ---: |
| Fresh application import | 540.874 |
| Provider import into existing schema | 44.035 |
| Application reimport | 48.739 |
| Read-only comparison | 40.531 |

Environment: Python 3.12.15, DuckDB 2.0.0, pytz 2026.5, package 0.2.0; Linux 7.0.0-1017-azure x86_64/glibc 2.39; four reported logical CPUs on a shared GitHub runner. Database settings are two threads and a 256 MiB memory limit. The temporary filesystem was not characterized. There is no dedicated hardware, explicit warmup/cache reset, comparative baseline or concurrent client load.

The full validation suite contains 73 Python test methods across contract, storage, CLI, scope, comparison, report and benchmark behavior, plus four real Chromium report checks. CI installs the built package on Python 3.11, 3.12 and 3.13. These counts describe executable checks, not exhaustive security coverage or real-provider accuracy. No live provider export or bill has been validated.
