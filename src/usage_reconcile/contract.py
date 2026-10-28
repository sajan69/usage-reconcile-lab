"""Bounded strict JSON and versioned source contracts. No network operations."""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

MAX_BYTES = 10 * 1024 * 1024
MAX_ROWS = 20_000
MAX_MANIFEST = 64 * 1024
MAX_COUNT = 10**12
TOKEN_FIELDS = ("input_tokens", "output_tokens", "input_cached_tokens")
MEDIA_FIELDS = ("input_audio_tokens", "input_cached_audio_tokens", "output_audio_tokens",
                "input_image_tokens", "input_cached_image_tokens", "output_image_tokens")
LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,127}\Z")
ISO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})\Z")


class InputError(ValueError):
    """Safe code only; never include raw input or paths in operational errors."""


def require(condition, code="invalid_input"):
    if not condition:
        raise InputError(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate_json_key")
        result[key] = value
    return result


def _constant(_):
    raise InputError("nonfinite_json")


def _float(value):
    # Counts/timestamps are integers. Reject all nonfinite floating encodings,
    # including overflow, even in fields that would otherwise be discarded.
    import math
    number = float(value)
    require(math.isfinite(number), "nonfinite_json")
    return number


def parse_json(raw):
    try:
        return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_pairs,
                          parse_constant=_constant, parse_float=_float)
    except (UnicodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        if isinstance(exc, InputError):
            raise
        raise InputError("invalid_json") from None


def read_bytes(path, cap=MAX_BYTES):
    with Path(path).open("rb") as handle:
        raw = handle.read(cap + 1)
    require(len(raw) <= cap, "input_too_large")
    return raw


def utc(value):
    require(isinstance(value, str) and ISO.fullmatch(value) is not None, "invalid_timestamp")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        raise InputError("invalid_timestamp") from None
    require(2000 <= dt.year <= 2100, "timestamp_out_of_range")
    return dt


def iso(dt):
    return dt.isoformat(timespec="microseconds").replace("+00:00", "Z")


def count(value, nullable=False):
    if value is None and nullable:
        return None
    require(type(value) is int and 0 <= value <= MAX_COUNT, "invalid_count")
    return value


def label(value, nullable=False):
    if value is None and nullable:
        return None
    require(isinstance(value, str) and LABEL.fullmatch(value) is not None, "invalid_label")
    return value


def tokens(row):
    vals = [count(row.get(key), nullable=True) for key in TOKEN_FIELDS]
    require(vals[2] is None or (vals[0] is not None and vals[2] <= vals[0]),
            "cached_exceeds_input")
    return dict(zip(TOKEN_FIELDS, vals))


@dataclass(frozen=True)
class Manifest:
    value: dict

    @classmethod
    def parse(cls, obj):
        fields = {"schema_version", "source", "start_utc", "end_utc", "acquired_at",
                  "coverage", "group_by", "batch", "modalities"}
        require(isinstance(obj, dict) and set(obj) == fields, "invalid_manifest_fields")
        require(type(obj["schema_version"]) is int and obj["schema_version"] == 1,
                "unsupported_manifest")
        require(obj["source"] in ("app_events_v1", "openai_completions_v1"), "unsupported_source")
        start, end, acquired = (utc(obj[k]) for k in ("start_utc", "end_utc", "acquired_at"))
        require(start.hour == start.minute == start.second == start.microsecond == 0 and
                end.hour == end.minute == end.second == end.microsecond == 0,
                "window_not_utc_days")
        require(1 <= (end - start).days <= 31 and acquired >= end, "invalid_window")
        require(obj["coverage"] in ("complete", "partial", "unknown"), "invalid_coverage")
        groups = obj["group_by"]
        require(isinstance(groups, list) and len(groups) <= 2 and
                all(isinstance(g, str) and g in ("project_id", "model") for g in groups) and
                len(set(groups)) == len(groups), "unsupported_grouping")
        if obj["source"] == "app_events_v1":
            require(set(groups) == {"project_id", "model"}, "unsupported_grouping")
        require(obj["batch"] is False and obj["modalities"] == ["text"], "unsupported_scope")
        normalized = {**obj, "start_utc": iso(start), "end_utc": iso(end),
                      "acquired_at": iso(acquired), "group_by": sorted(groups)}
        return cls(normalized)

    def contains(self, dt):
        return utc(self.value["start_utc"]) <= dt < utc(self.value["end_utc"])


@dataclass(frozen=True)
class Parsed:
    manifest: Manifest
    raw_sha256: str
    rows: tuple
    quality: dict
    buckets: tuple = ()


def app_events(raw, manifest):
    require(len(raw) <= MAX_BYTES, "input_too_large")
    require(manifest.value["source"] == "app_events_v1", "wrong_source")
    # Byte splitting preserves per-line UTF-8 validation; blank lines are not records.
    lines = raw.splitlines()
    require(len(lines) <= MAX_ROWS, "too_many_records")
    seen, duplicates, ignored, missing, response_ids = {}, 0, 0, 0, set()
    repeated_responses = 0
    for line in lines:
        if not line.strip():
            continue
        require(len(line) <= 64 * 1024, "record_too_large")
        obj = parse_json(line)
        require(isinstance(obj, dict), "invalid_event")
        event_id = label(obj.get("event_id"))
        at = utc(obj.get("occurred_at"))
        require(manifest.contains(at), "event_outside_window")
        response = label(obj.get("response_id"), nullable=True)
        require("usage" in obj, "missing_usage_field")
        usage = obj["usage"]
        require(usage is None or isinstance(usage, dict), "invalid_usage")
        row = dict(event_id=event_id, response_id=response, occurred_at=iso(at),
                   project_id=label(obj.get("project_id")), model=label(obj.get("model")),
                   **tokens(usage or {}))
        if event_id in seen:
            require(seen[event_id] == row, "conflicting_event_id")
            duplicates += 1
            continue
        seen[event_id] = row
        if response is not None:
            repeated_responses += response in response_ids
            response_ids.add(response)
        missing += row["input_tokens"] is None or row["output_tokens"] is None
        ignored += len(set(obj) - {"event_id", "response_id", "occurred_at", "project_id", "model", "usage"})
        if usage is not None:
            ignored += len(set(usage) - set(TOKEN_FIELDS))
    quality = dict(input_records=len(seen) + duplicates, records=len(seen),
                   exact_projected_duplicates=duplicates, unknown_usage_records=missing,
                   repeated_response_ids=repeated_responses, ignored_fields=ignored)
    return Parsed(manifest, digest(raw), tuple(seen.values()), quality)


def provider_pages(raw, manifest):
    require(len(raw) <= MAX_BYTES, "input_too_large")
    require(manifest.value["source"] == "openai_completions_v1", "wrong_source")
    obj = parse_json(raw)
    require(isinstance(obj, dict) and set(obj) == {"pages"} and isinstance(obj["pages"], list),
            "invalid_page_bundle")
    pages = obj["pages"]
    require(1 <= len(pages) <= 31, "invalid_page_count")
    rows, buckets, cursors, last_end, result_keys = [], [], set(), None, set()
    partial = False
    for index, page in enumerate(pages):
        require(isinstance(page, dict) and page.get("object") == "page" and
                type(page.get("has_more")) is bool and isinstance(page.get("data"), list),
                "invalid_page")
        # Page linkage is not attested: ordered response bodies alone cannot prove
        # that the collector actually used each previous cursor.
        next_page = page.get("next_page")
        require((page["has_more"] and isinstance(next_page, str) and 1 <= len(next_page) <= 512) or
                (page["has_more"] is False and next_page is None), "invalid_cursor")
        require(index == len(pages) - 1 or page["has_more"], "page_after_final")
        if next_page is not None:
            require(next_page not in cursors, "repeated_cursor")
            cursors.add(next_page)
        partial = page["has_more"]
        for bucket in page["data"]:
            require(isinstance(bucket, dict) and bucket.get("object") == "bucket" and
                    isinstance(bucket.get("results"), list), "invalid_bucket")
            start, end = bucket.get("start_time"), bucket.get("end_time")
            require(type(start) is int and type(end) is int and
                    946684800 <= start < end <= 4133980800 and
                    start % 86400 == 0 and end - start == 86400, "unsupported_bucket")
            require(utc(manifest.value["start_utc"]).timestamp() <= start and
                    end <= utc(manifest.value["end_utc"]).timestamp(), "bucket_outside_window")
            require(last_end is None or start >= last_end, "overlapping_buckets")
            last_end = end
            buckets.append((start, end))
            require(len(buckets) <= 31, "too_many_buckets")
            for result in bucket["results"]:
                require(isinstance(result, dict) and
                        result.get("object") == "organization.usage.completions.result", "wrong_result_type")
                # Scope declarations matter: absent optional media fields are not evidence of text-only use.
                for field in MEDIA_FIELDS:
                    if field in result:
                        require(count(result[field], nullable=True) in (0, None), "unsupported_media")
                require(result.get("batch") in (None, False) and
                        type(result.get("batch")) in (bool, type(None)), "unsupported_batch")
                for dimension in ("service_tier", "api_source", "user_id", "api_key_id"):
                    require(result.get(dimension) is None, "unsupported_dimension")
                dims = {}
                for dim in ("project_id", "model"):
                    value = result.get(dim)
                    if dim in manifest.value["group_by"]:
                        dims[dim] = label(value)
                    else:
                        require(value is None, "unexpected_dimension")
                        dims[dim] = None
                row = dict(start_time=start, end_time=end, **dims,
                           num_model_requests=count(result.get("num_model_requests")), **tokens(result))
                # Required totals cannot silently become unknown through omitted provider fields.
                require(row["input_tokens"] is not None and row["output_tokens"] is not None,
                        "missing_provider_totals")
                key = (start, dims["project_id"], dims["model"])
                require(key not in result_keys, "duplicate_provider_group")
                result_keys.add(key)
                rows.append(row)
                require(len(rows) <= MAX_ROWS, "too_many_records")
    start = int(utc(manifest.value["start_utc"]).timestamp())
    end = int(utc(manifest.value["end_utc"]).timestamp())
    covered = {a for a, _ in buckets}
    gaps = sum(day not in covered for day in range(start, end, 86400))
    require(manifest.value["coverage"] != "complete" or (not partial and gaps == 0),
            "incomplete_provider_capture")
    quality = dict(records=len(rows), buckets=len(buckets), missing_daily_buckets=gaps,
                   has_more=partial, coverage_declared=manifest.value["coverage"],
                   capture_attested=False)
    return Parsed(manifest, digest(raw), tuple(rows), quality, tuple(buckets))


def parse_input(data_path, manifest_path):
    manifest = Manifest.parse(parse_json(read_bytes(manifest_path, MAX_MANIFEST)))
    raw = read_bytes(data_path)
    return (app_events if manifest.value["source"] == "app_events_v1" else provider_pages)(raw, manifest)
