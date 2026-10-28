"""Render adversarial internal report labels for browser defense-in-depth checks."""
import json
from pathlib import Path
from usage_reconcile.reports import render
root = Path(__file__).resolve().parent / "artifacts"
report = json.loads((root / "comparison.json").read_text())
report["rows"][0]["project_id"] = '<img src=x onerror="alert(1)">'
report["warnings"].append('</pre><script>alert(1)</script>')
(root / "probe.html").write_bytes(render(report, "html"))
