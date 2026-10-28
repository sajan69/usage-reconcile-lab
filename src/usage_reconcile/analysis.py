"""Read-only conditional daily comparisons, without zero-filling absent groups."""
from datetime import timedelta, datetime, timezone
from importlib.resources import files
import json
import re
from .contract import Manifest, require, utc
from .storage import connect, _schema

MAX_GROUPS = 2000
WARNINGS = ["Scope, coverage and timestamp attribution are caller declarations, not provider attestation.",
            "Different acquisition times and late provider updates can change observations.",
            "Differences are not evidence of billing errors or invoice amounts."]


def _metadata(con, sid, source):
    require(isinstance(sid, str) and re.fullmatch(r"[0-9a-f]{64}", sid) is not None, "invalid_snapshot_id")
    row = con.execute("SELECT source,raw_sha256,projection_sha256,manifest_json,quality_json "
                      "FROM snapshots WHERE id=?", [sid]).fetchone()
    require(row is not None, "snapshot_missing")
    require(row[0] == source, "wrong_snapshot_source")
    manifest = Manifest.parse(json.loads(row[3])).value
    return dict(snapshot_id=sid, source=row[0], raw_sha256=row[1], projection_sha256=row[2],
                manifest=manifest, quality=json.loads(row[4]))


def compare(db_path, app_id, provider_id):
    with connect(db_path, read_only=True) as con:
        con.execute("BEGIN TRANSACTION")
        try:
            _schema(con)
            app = _metadata(con, app_id, "app_events_v1")
            provider = _metadata(con, provider_id, "openai_completions_v1")
            report = _compare(con, app, provider)
            con.execute("COMMIT")
            return report
        except BaseException:
            con.execute("ROLLBACK")
            raise


def _compare(con, app, provider):
    a, p = app["manifest"], provider["manifest"]
    blockers = []
    if (a["start_utc"], a["end_utc"]) != (p["start_utc"], p["end_utc"]):
        blockers.append("window_mismatch")
    if a["group_by"] != p["group_by"]:
        blockers.append("grouping_mismatch")
    scope_a, scope_p = a.get("reconciliation"), p.get("reconciliation")
    if not scope_a or not scope_p:
        blockers.append("missing_reconciliation_scope")
    elif scope_a != scope_p:
        blockers.append("scope_mismatch")
    elif scope_a["time_basis"] != "provider_utc_bucket":
        blockers.append("unmatched_timestamp_basis")
    report = dict(report_version=1, status="blocked" if blockers else "conditional",
                  blockers=blockers, warnings=list(WARNINGS), app=app, provider=provider, rows=[])
    if blockers:
        report["summary"] = dict(groups=0, comparable_metrics=0, withheld_metrics=0)
        return report
    days = (utc(a["end_utc"]) - utc(a["start_utc"])).days
    require(days * len(scope_a["project_ids"]) * len(scope_a["models"]) <= MAX_GROUPS,
            "comparison_group_limit")
    cursor = con.execute(files("usage_reconcile").joinpath("sql/daily.sql").read_text(),
                         dict(app=app["snapshot_id"], provider=provider["snapshot_id"]))
    columns = [col[0] for col in cursor.description]
    observed = [dict(zip(columns, row)) for row in cursor.fetchall()]
    require(len(observed) <= MAX_GROUPS, "comparison_group_limit")
    lookup = {(r["day"], r["project_id"], r["model"]): r for r in observed}
    covered = {datetime.fromtimestamp(row[0], timezone.utc).date().isoformat() for row in
               con.execute("SELECT start_time FROM provider_buckets WHERE snapshot_id=?",
                           [provider["snapshot_id"]]).fetchall()}
    global_reasons = []
    if a["coverage"] != "complete": global_reasons.append("app_coverage_not_complete")
    if p["coverage"] != "complete": global_reasons.append("provider_coverage_not_complete")
    comparable = withheld = 0
    for offset in range(days):
        day = (utc(a["start_utc"]) + timedelta(days=offset)).date().isoformat()
        for project in scope_a["project_ids"]:
            for model in scope_a["models"]:
                key = (day, project, model)
                obs = lookup.get(key, dict.fromkeys(columns))
                reasons = list(global_reasons)
                if day not in covered: reasons.append("provider_bucket_missing")
                if obs["app_events"] is None: reasons.append("app_group_unobserved")
                if obs["provider_requests"] is None: reasons.append("provider_group_unobserved")
                if obs["ambiguous_responses"]: reasons.append("repeated_response_identity")
                if obs["unidentified_responses"]: reasons.append("missing_response_identity")
                metrics = {}
                for name, app_field, provider_field, unknown_field in (
                    ("requests", "app_events", "provider_requests", None),
                    ("input_tokens", "app_known_input", "provider_input", "unknown_input"),
                    ("output_tokens", "app_known_output", "provider_output", "unknown_output"),
                    ("cached_input_tokens", "app_known_cached", "provider_cached", "unknown_cached")):
                    metric_reasons = list(reasons)
                    if unknown_field and obs[unknown_field]: metric_reasons.append("app_usage_unknown")
                    if obs[provider_field] is None: metric_reasons.append("provider_value_unknown")
                    # A known subtotal is exposed separately, never as a complete app total.
                    app_value = obs[app_field] if not unknown_field or obs[unknown_field] == 0 else None
                    if app_value is None: metric_reasons.append("app_value_unknown")
                    metric_reasons = sorted(set(metric_reasons))
                    allowed = not metric_reasons
                    comparable += allowed
                    withheld += not allowed
                    metrics[name] = dict(app=app_value, provider=obs[provider_field],
                                         difference_provider_minus_app=(obs[provider_field] - app_value)
                                         if allowed else None,
                                         status="conditional_comparison" if allowed else "withheld",
                                         reasons=metric_reasons)
                report["rows"].append(dict(day=day, project_id=project, model=model,
                    provider_bucket_present=day in covered, observations=obs, metrics=metrics))
    report["summary"] = dict(groups=len(report["rows"]), comparable_metrics=comparable, withheld_metrics=withheld)
    return report
