"""Small local CLI with sanitized operational errors and JSON output."""
import argparse
import json
import sys
import duckdb
from . import __version__
from .contract import InputError
from .demo import generate, demo
from .storage import ingest, inspect


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
    commands.add_parser("demo", help="run isolated synthetic ingestion checks")
    args = parser.parse_args(argv)
    try:
        if args.command == "generate": result = generate(args.out)
        elif args.command == "ingest": result = ingest(args.input, args.manifest, args.db)
        elif args.command == "inspect": result = inspect(args.db, args.snapshot)
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
