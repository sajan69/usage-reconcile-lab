"""Atomic, immutable, data-minimized DuckDB observations in trusted local files."""
from contextlib import contextmanager
import os
from pathlib import Path
import re
import stat
import duckdb
from .contract import InputError, canonical, digest, require, parse_input

SCHEMA = "usage-reconcile-v1"
CONFIG = {"enable_external_access": "false", "autoload_known_extensions": "false",
          "autoinstall_known_extensions": "false", "threads": "2", "memory_limit": "256MB"}
DDL = (
    "CREATE TABLE lab_meta (version VARCHAR PRIMARY KEY)",
    "CREATE TABLE snapshots (id VARCHAR PRIMARY KEY, source VARCHAR NOT NULL, raw_sha256 VARCHAR NOT NULL, "
    "projection_sha256 VARCHAR NOT NULL, manifest_json VARCHAR NOT NULL, quality_json VARCHAR NOT NULL)",
    "CREATE TABLE app_events (snapshot_id VARCHAR NOT NULL REFERENCES snapshots(id), event_id VARCHAR NOT NULL, "
    "response_id VARCHAR, occurred_at TIMESTAMPTZ NOT NULL, project_id VARCHAR NOT NULL, model VARCHAR NOT NULL, "
    "input_tokens BIGINT, output_tokens BIGINT, input_cached_tokens BIGINT, PRIMARY KEY(snapshot_id,event_id))",
    "CREATE TABLE provider_buckets (snapshot_id VARCHAR NOT NULL REFERENCES snapshots(id), "
    "start_time BIGINT NOT NULL, end_time BIGINT NOT NULL, PRIMARY KEY(snapshot_id,start_time))",
    "CREATE TABLE provider_usage (snapshot_id VARCHAR NOT NULL REFERENCES snapshots(id), start_time BIGINT NOT NULL, "
    "end_time BIGINT NOT NULL, project_id VARCHAR, model VARCHAR, num_model_requests BIGINT NOT NULL, "
    "input_tokens BIGINT NOT NULL, output_tokens BIGINT NOT NULL, input_cached_tokens BIGINT)"
)


def _check_path(path, create):
    path = Path(path).absolute()
    try:
        info = path.lstat()
    except FileNotFoundError:
        require(create, "database_missing")
        # Pre-create with restrictive permissions; trusted parent directory required.
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        return path, True
    require(stat.S_ISREG(info.st_mode), "unsafe_database_path")
    if os.name == "posix":
        require(info.st_mode & 0o077 == 0, "unsafe_database_permissions")
    require(info.st_size > 0, "empty_database_file")
    return path, False


@contextmanager
def connect(path, *, create=False, read_only=False):
    path, new = _check_path(path, create)
    # DuckDB accepts an empty pre-created file only after unlinking it. Keep parent
    # trusted: path checks are not an adversarial-filesystem sandbox.
    if new:
        path.unlink()
        old_mask = os.umask(0o077)
        try:
            con = duckdb.connect(str(path), config=CONFIG)
        finally:
            os.umask(old_mask)
    else:
        con = duckdb.connect(str(path), read_only=read_only, config=CONFIG)
    try:
        con.execute("SET TimeZone='UTC'")
        yield con
    finally:
        con.close()


def _schema(con, allow_create=False):
    tables = {row[0] for row in con.execute("SELECT table_name FROM information_schema.tables "
                                         "WHERE table_schema='main'").fetchall()}
    if not tables and allow_create:
        for sql in DDL:
            con.execute(sql)
        con.execute("INSERT INTO lab_meta VALUES (?)", [SCHEMA])
    else:
        require(tables == {"lab_meta", "snapshots", "app_events", "provider_buckets", "provider_usage"},
                "unsupported_database")
        require(con.execute("SELECT version FROM lab_meta").fetchall() == [(SCHEMA,)], "unsupported_database")


def _insert_rows(con, snapshot_id, parsed):
    if parsed.manifest.value["source"] == "app_events_v1":
        if parsed.rows:
            con.executemany("INSERT INTO app_events VALUES (?,?,?,?,?,?,?,?,?)", [
                [snapshot_id, r["event_id"], r["response_id"], r["occurred_at"], r["project_id"], r["model"],
                 r["input_tokens"], r["output_tokens"], r["input_cached_tokens"]] for r in parsed.rows])
    else:
        if parsed.buckets:
            con.executemany("INSERT INTO provider_buckets VALUES (?,?,?)",
                            [[snapshot_id, *bucket] for bucket in parsed.buckets])
        if parsed.rows:
            con.executemany("INSERT INTO provider_usage VALUES (?,?,?,?,?,?,?,?,?)", [
                [snapshot_id, r["start_time"], r["end_time"], r["project_id"], r["model"],
                 r["num_model_requests"], r["input_tokens"], r["output_tokens"], r["input_cached_tokens"]]
                for r in parsed.rows])


def ingest(data_path, manifest_path, db_path):
    # All parsing/normalization/deduplication finishes before the DB is opened.
    parsed = parse_input(data_path, manifest_path)
    manifest_json = canonical(parsed.manifest.value)
    snapshot_id = digest(canonical(dict(adapter=SCHEMA, manifest=parsed.manifest.value,
                                        raw_sha256=parsed.raw_sha256)).encode())
    projection_sha = digest(canonical(dict(rows=parsed.rows, buckets=parsed.buckets)).encode())
    with connect(db_path, create=True) as con:
        con.execute("BEGIN TRANSACTION")
        try:
            _schema(con, allow_create=True)
            exists = con.execute("SELECT id FROM snapshots WHERE id=?", [snapshot_id]).fetchone()
            if exists:
                inserted = 0
            else:
                con.execute("INSERT INTO snapshots VALUES (?,?,?,?,?,?)", [snapshot_id,
                    parsed.manifest.value["source"], parsed.raw_sha256, projection_sha,
                    manifest_json, canonical(parsed.quality)])
                _insert_rows(con, snapshot_id, parsed)
                inserted = len(parsed.rows)
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
    return dict(snapshot_id=snapshot_id, inserted=inserted, reused=bool(exists), quality=parsed.quality)


def inspect(db_path, snapshot_id):
    import json
    require(isinstance(snapshot_id, str) and re.fullmatch(r"[0-9a-f]{64}", snapshot_id) is not None,
            "invalid_snapshot_id")
    with connect(db_path, read_only=True) as con:
        con.execute("BEGIN TRANSACTION")
        try:
            _schema(con)
            row = con.execute("SELECT source,raw_sha256,projection_sha256,manifest_json,quality_json "
                              "FROM snapshots WHERE id=?", [snapshot_id]).fetchone()
            require(row is not None, "snapshot_missing")
            report = dict(snapshot_id=snapshot_id, source=row[0], raw_sha256=row[1],
                          projection_sha256=row[2], manifest=json.loads(row[3]), quality=json.loads(row[4]))
            con.execute("COMMIT")
            return report
        except BaseException:
            con.execute("ROLLBACK")
            raise
