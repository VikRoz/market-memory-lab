"""Structured output contract and a minimal inherited environment."""
from market_memory_lab.model import ValidationError

OUTPUT_SCHEMA = {"type": "object", "additionalProperties": False,
    "required": ["title", "interpretation", "observations", "rule_references"],
    "properties": {"title": {"type": "string"}, "interpretation": {"type": "string"},
        "observations": {"type": "array", "items": {"type": "object", "additionalProperties": False,
            "required": ["metric", "previous", "current", "change", "unit", "evidence_ids"],
            "properties": {**{k: {"type": "string"} for k in ("metric", "previous", "current", "change", "unit")},
                           "evidence_ids": {"type": "array", "items": {"type": "string"}}}}},
        "rule_references": {"type": "array", "items": {"type": "string"}}}}


def require(ok, reason):
    if not ok:
        raise ValidationError(reason)


def child_environment(environment):
    # Do not inspect secrets or mutate HOME/CODEX_HOME: forward these two unchanged.
    # No API keys, DB/SSH/proxy/provider variables, NODE_OPTIONS, or private PYTHONPATH.
    return {k: v for k, v in environment.items()
            if k in {"PATH", "HOME", "CODEX_HOME", "TMPDIR", "LANG", "LC_ALL", "SYSTEMROOT"}}
