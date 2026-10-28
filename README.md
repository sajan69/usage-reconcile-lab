# Usage Reconcile Lab

An offline Python/DuckDB tool for comparing application AI usage telemetry with OpenAI Completions Usage API response exports. Missing measurements remain SQL `NULL`; recorded zero stays zero. Each import creates an immutable observation with source context, hashes and quality counts.

Python 3.11+ is required. No provider credentials, network calls or paid services are used.

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install .
usage-reconcile demo
python -m unittest discover -v
```

To retain the synthetic inputs and import them into a local database:

```sh
mkdir -p data
usage-reconcile generate --out data/example
usage-reconcile ingest data/example/app.jsonl --manifest data/example/app-manifest.json --db data/example.duckdb
usage-reconcile ingest data/example/provider.json --manifest data/example/provider-manifest.json --db data/example.duckdb
usage-reconcile inspect --db data/example.duckdb --snapshot <snapshot_id_from_ingest>
```

The ingestion demo checks seven synthetic input events → six unique event IDs, one unknown-usage event, one repeated response ID and two provider buckets. Reimporting the same file/context inserts zero rows.

For an end-to-end comparison and local reports:

```sh
usage-reconcile demo-comparison --out data/comparison-reports
# Open data/comparison-reports/comparison.html in a browser.
usage-reconcile compare --db DB --app APP_SNAPSHOT --provider PROVIDER_SNAPSHOT
usage-reconcile export --db DB --app APP_SNAPSHOT --provider PROVIDER_SNAPSHOT --format csv --out NEW_REPORT.csv
```

Comparisons require matching explicit population/time declarations and fully grouped project/model observations. Missing data, ambiguous response identities and incomplete coverage withhold differences. Known subtotals stay separate from complete totals. No absent group becomes zero. The comparison demo checks nine synthetic events across six daily groups: 13 conditional metric comparisons and 11 withheld values. It includes a recorded zero difference and a two-token observed difference. These are exact fixture checks, not real-provider accuracy or billing findings. Workload timings are not measured in this version.

See [input contracts](docs/INPUTS.md) for source fields, scope declarations and limits, [comparison/report semantics and worked findings](docs/COMPARISONS.md), and [storage behavior](docs/STORAGE.md) for transactions and security assumptions. The [CI workflow](.github/workflows/ci.yml) tests Python 3.11–3.13.

Real exports, credentials, prompts and completions must remain outside Git. The database is not encrypted. Usage observations do not certify an invoice or establish a billing error.
