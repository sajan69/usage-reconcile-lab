"""Small local CLI with sanitized operational errors and JSON output."""
import argparse
import json
import sys
import duckdb
from . import __version__
from .contract import InputError
from .demo import generate, demo, comparison_demo
from .storage import ingest, inspect
from .analysis import compare
from .reports import export
from .benchmark import benchmark


def main(argv=None):
    parser = argparse.ArgumentParser(prog="usage-reconcile")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("generate", help="create synthetic inputs in a new directory")
    create.add_argument("--out", required=True)
    load = commands.add_parser("ingest", help="validate and import one immutable observation")
    load.add_argument("input")
    load.add_argument("--manifest", required=True)
    load.add_argument("--db", required=True)
    info = commands.add_parser("inspect", help="read one observation's provenance and quality counts")
    info.add_argument("--db", required=True)
    info.add_argument("--snapshot", required=True)
    analysis = commands.add_parser("compare", help="compare declared matching usage populations")
    analysis.add_argument("--db", required=True)
    analysis.add_argument("--app", required=True)
    analysis.add_argument("--provider", required=True)
    output = commands.add_parser("export", help="write one bounded comparison report without overwrite")
    output.add_argument("--db", required=True)
    output.add_argument("--app", required=True)
    output.add_argument("--provider", required=True)
    output.add_argument("--format", choices=["json", "csv", "html"], required=True)
    output.add_argument("--out", required=True)
    commands.add_parser("demo", help="run isolated synthetic ingestion checks")
    comparison = commands.add_parser("demo-comparison", help="run exact synthetic comparison and optional reports")
    comparison.add_argument("--out", help="new directory for JSON/CSV/HTML reports")
    workload = commands.add_parser("benchmark", help="measure bounded synthetic imports and comparisons")
    workload.add_argument("--rows", type=int, default=800)
    workload.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args(argv)
    try:
        if args.command == "generate": result = generate(args.out)
        elif args.command == "ingest": result = ingest(args.input, args.manifest, args.db)
        elif args.command == "inspect": result = inspect(args.db, args.snapshot)
        elif args.command == "compare": result = compare(args.db, args.app, args.provider)
        elif args.command == "export":
            result = export(compare(args.db, args.app, args.provider), args.out, args.format)
        elif args.command == "demo-comparison": result = comparison_demo(args.out)
        elif args.command == "benchmark": result = benchmark(args.rows, args.repeats)
        else: result = demo()
        print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
        return 0
    except InputError as error:
        code = str(error)
    except OSError:
        code = "local_file_error"
    except duckdb.Error:
        code = "database_error"
    print(json.dumps({"error": code}), file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
