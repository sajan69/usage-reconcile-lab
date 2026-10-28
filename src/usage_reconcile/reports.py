"""Bounded data-only exports and a script-free, escaped local HTML report."""
import csv
import html
import io
import json
import os
import unicodedata
from .contract import canonical, require

MAX_REPORT_BYTES = 2 * 1024 * 1024


def _cell(value):
    if value is None:
        return ""
    if not isinstance(value, str):
        return value
    probe = unicodedata.normalize("NFKC", value).lstrip()
    if probe.startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")):
        return "'" + value
    return value


def _csv(report):
    columns = ["app_snapshot", "provider_snapshot", "app_raw_sha256", "provider_raw_sha256",
               "app_acquired_at", "provider_acquired_at", "start_utc", "end_utc", "scope_json",
               "provider_start_utc", "provider_end_utc", "provider_scope_json", "app_group_by", "provider_group_by",
               "app_coverage", "provider_coverage", "warnings", "blockers", "day", "project_id", "model",
               "metric", "app_value", "provider_value", "difference_provider_minus_app", "status", "reasons",
               "app_events", "unknown_input", "unknown_output", "unknown_cached", "app_known_input",
               "app_known_output", "app_known_cached", "provider_bucket_present"]
    out = io.StringIO(newline="")
    writer = csv.writer(out, quoting=csv.QUOTE_ALL)
    writer.writerow(columns)
    a, p = report["app"], report["provider"]
    context = dict(app_snapshot=a["snapshot_id"], provider_snapshot=p["snapshot_id"],
                   app_raw_sha256=a["raw_sha256"], provider_raw_sha256=p["raw_sha256"],
                   app_acquired_at=a["manifest"]["acquired_at"], provider_acquired_at=p["manifest"]["acquired_at"],
                   start_utc=a["manifest"]["start_utc"], end_utc=a["manifest"]["end_utc"],
                   scope_json=canonical(a["manifest"].get("reconciliation")),
                   provider_start_utc=p["manifest"]["start_utc"], provider_end_utc=p["manifest"]["end_utc"],
                   provider_scope_json=canonical(p["manifest"].get("reconciliation")),
                   app_group_by=canonical(a["manifest"]["group_by"]), provider_group_by=canonical(p["manifest"]["group_by"]),
                   app_coverage=a["manifest"]["coverage"], provider_coverage=p["manifest"]["coverage"],
                   warnings=" | ".join(report["warnings"]), blockers=" | ".join(report["blockers"]))
    if report["blockers"]:
        item = dict(context, status="blocked", reasons=" | ".join(report["blockers"]))
        writer.writerow([_cell(item.get(key)) for key in columns])
    for row in report["rows"]:
        for metric, values in row["metrics"].items():
            item = dict(context, **{k: row[k] for k in ("day", "project_id", "model", "provider_bucket_present")},
                        **{k: row["observations"].get(k) for k in ("app_events", "unknown_input", "unknown_output",
                           "unknown_cached", "app_known_input", "app_known_output", "app_known_cached")},
                        metric=metric, app_value=values["app"], provider_value=values["provider"],
                        difference_provider_minus_app=values["difference_provider_minus_app"],
                        status=values["status"], reasons=" | ".join(values["reasons"]))
            writer.writerow([_cell(item.get(key)) for key in columns])
    return out.getvalue()


def _html(report):
    escape = lambda value: html.escape(str(value), quote=True)
    def display(value):
        return "Unknown / unobserved" if value is None else escape(value)
    parts = ['<!doctype html><html lang="en"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width,initial-scale=1">',
             '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">',
             '<meta name="referrer" content="no-referrer"><title>Usage comparison</title>',
             '<style>body{font:16px/1.5 system-ui,sans-serif;background:#f5f7fa;color:#172638;margin:0}'
             'main{max-width:1100px;margin:auto;padding:24px}h1{line-height:1.15}section,details{background:white;'
             'border:1px solid #cbd5e1;border-radius:10px;padding:18px;margin:16px 0}table{border-collapse:collapse;'
             'width:100%;font-size:14px}td,th{text-align:left;padding:10px;border-bottom:1px solid #dce3ea;'
             'vertical-align:top}th{background:#eaf0f7}caption{text-align:left;font-weight:bold;margin:12px 0}'
             '.scroll{overflow:auto}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}'
             'summary{cursor:pointer;font-weight:bold}.withheld{color:#835000}.note{font-weight:600}'
             '@media(max-width:500px){main{padding:12px}section,details{padding:12px}}</style></head><body><main>',
             '<h1>Usage comparison</h1>', '<p class="note">Differences are conditional observations, not billing findings.</p>',
             '<section><h2>Comparison context</h2><p>Status: ' + escape(report["status"]) + '</p>',
             '<p>' + escape(report["summary"]["groups"]) + ' declared daily groups; ' +
             escape(report["summary"]["comparable_metrics"]) + ' conditional metric comparisons; ' +
             escape(report["summary"]["withheld_metrics"]) + ' withheld.</p><ul>']
    for warning in report["warnings"]:
        parts.append('<li>' + escape(warning) + '</li>')
    parts.append('</ul>')
    if report["blockers"]:
        parts.append('<h3>Comparison blocked</h3><ul>' + ''.join('<li>' + escape(x) + '</li>'
                     for x in report["blockers"]) + '</ul>')
    parts.append('</section>')
    for row in report["rows"]:
        parts.append('<section><h2>' + escape(row["day"]) + ' · ' + escape(row["project_id"]) +
                     ' · ' + escape(row["model"]) + '</h2>')
        parts.append('<p>Provider daily bucket: ' + ('present' if row["provider_bucket_present"] else 'missing') +
                     '. An absent group is not a recorded zero.</p>')
        parts.append('<div class="scroll" tabindex="0" role="region" aria-label="Metric comparisons">'
                     '<table><caption>Provider minus application</caption><thead><tr>'
                     '<th scope="col">Metric</th><th scope="col">Application total</th>'
                     '<th scope="col">Provider total</th><th scope="col">Difference</th>'
                     '<th scope="col">Status / reasons</th></tr></thead><tbody>')
        for metric, value in row["metrics"].items():
            status = value["status"] + (": " + ", ".join(value["reasons"]) if value["reasons"] else "")
            parts.append('<tr><th scope="row">' + escape(metric) + '</th><td>' + display(value["app"]) +
                         '</td><td>' + display(value["provider"]) + '</td><td>' +
                         display(value["difference_provider_minus_app"]) + '</td><td class="withheld">' +
                         escape(status) + '</td></tr>')
        parts.append('</tbody></table></div><details><summary>Known subtotals and unknown counts</summary><pre>' +
                     escape(json.dumps(row["observations"], indent=2, sort_keys=True)) + '</pre></details></section>')
    provenance = {key: report[key] for key in ("app", "provider", "warnings", "blockers")}
    parts.append('<details><summary>Snapshot provenance and quality</summary><pre>' +
                 escape(json.dumps(provenance, indent=2, sort_keys=True)) + '</pre></details>'
                 '</main></body></html>')
    return ''.join(parts)


def render(report, format):
    require(format in ("json", "csv", "html"), "unsupported_report_format")
    require(isinstance(report.get("rows"), list) and len(report["rows"]) <= 2000, "comparison_group_limit")
    if format == "json": text = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    elif format == "csv": text = _csv(report)
    else: text = _html(report)
    raw = text.encode("utf-8")
    require(len(raw) <= MAX_REPORT_BYTES, "report_too_large")
    return raw


def export(report, out, format):
    raw = render(report, format)  # Validate/serialize/cap before creating any file.
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(raw)
    return dict(format=format, bytes=len(raw), groups=len(report["rows"]), status=report["status"])
