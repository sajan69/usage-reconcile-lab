# Usage Reconcile Lab

An offline Python/SQL project for comparing application AI usage telemetry with provider usage exports, keeping missing measurements distinct from recorded zero usage.

Comparisons are scoped to matching UTC time windows, project/model groupings and supported text-usage fields. Duplicate events, incomplete exports and incompatible aggregation levels must remain visible rather than producing misleading totals.

The repository currently contains this project introduction only; runnable code, setup instructions and evaluation results are not available yet.

No provider credentials or paid API calls are required for the intended local workflow. Real exports, prompts, completions and credentials do not belong in this repository. Usage comparisons are not invoice certification or evidence of billing errors.
