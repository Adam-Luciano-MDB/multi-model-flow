"""
Real token usage from Claude Code session transcripts.

A skill cannot observe the token counts of the sub-agents it spawns — that data
is harness telemetry, not visible to the orchestrating model. But Claude Code
writes every assistant turn (main session AND each sub-agent) to JSONL
transcripts on disk, each carrying the exact model and usage. This module parses
those transcripts to produce REAL per-tier token counts and cost, replacing the
call-count estimates used elsewhere.

Transcript layout (per project):
  ~/.claude/projects/<encoded-cwd>/<session>.jsonl              ← main session
  ~/.claude/projects/<encoded-cwd>/<session>/subagents/*.jsonl  ← sub-agents

Each assistant record: {"type": "assistant", "message": {"model": ..., "usage": {...}}}
"""
import glob
import json
import os

PROJECTS_DIR = os.path.expanduser("~/.claude/projects")

# Real per-million-token pricing (USD). Source: claude-api skill, cached 2026-06.
# (input_per_mtok, output_per_mtok)
_PRICING = {
    "opus":   (5.0, 25.0),    # claude-opus-4-8
    "sonnet": (3.0, 15.0),    # claude-sonnet-4-6
    "haiku":  (1.0, 5.0),     # claude-haiku-4-5
    "fable":  (10.0, 50.0),   # claude-fable-5
}
# Prompt-caching multipliers on the input price.
_CACHE_READ_MULT = 0.1     # cached tokens served back
_CACHE_WRITE_MULT = 1.25   # tokens written to cache (5-minute TTL)

_TIERS = ("opus", "sonnet", "haiku", "fable")


def model_to_tier(model_id: str) -> str:
    """Map a model ID (e.g. 'claude-sonnet-4-6') to its tier, or '' if unknown."""
    if not model_id:
        return ""
    mid = model_id.lower()
    for tier in _TIERS:
        if tier in mid:
            return tier
    return ""


def _iter_assistant_usage(jsonl_path: str):
    """Yield (tier, usage_dict) for each assistant record with usage in a file."""
    try:
        with open(jsonl_path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") != "assistant":
                    continue
                msg = rec.get("message")
                if not isinstance(msg, dict):
                    continue
                usage = msg.get("usage")
                if not isinstance(usage, dict):
                    continue
                tier = model_to_tier(msg.get("model", ""))
                if tier:
                    yield tier, usage
    except OSError:
        return


def find_active_project_dir():
    """Return the project dir whose most-recent transcript is newest, or None."""
    try:
        candidates = []
        for entry in os.scandir(PROJECTS_DIR):
            if not entry.is_dir():
                continue
            jsonls = glob.glob(os.path.join(entry.path, "*.jsonl"))
            if not jsonls:
                continue
            newest = max(os.path.getmtime(p) for p in jsonls)
            candidates.append((newest, entry.path))
        if not candidates:
            return None
        return max(candidates)[1]
    except OSError:
        return None


def _blank_tier() -> dict:
    return {
        "input_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "output_tokens": 0,
    }


def aggregate_real_usage(project_dir=None) -> dict:
    """
    Aggregate real token usage by tier from a project's transcripts.

    Reads the main session files plus all sub-agent transcripts (recursively),
    sums per-message usage by tier, and computes real cost using cache-aware
    pricing. Returns zeros (never raises) when no transcripts are found.
    """
    if project_dir is None:
        project_dir = find_active_project_dir()

    totals = {tier: _blank_tier() for tier in _TIERS}

    if project_dir and os.path.isdir(project_dir):
        for path in glob.glob(os.path.join(project_dir, "**", "*.jsonl"), recursive=True):
            for tier, usage in _iter_assistant_usage(path):
                t = totals[tier]
                t["input_tokens"] += int(usage.get("input_tokens", 0) or 0)
                t["cache_read_tokens"] += int(usage.get("cache_read_input_tokens", 0) or 0)
                t["cache_creation_tokens"] += int(usage.get("cache_creation_input_tokens", 0) or 0)
                t["output_tokens"] += int(usage.get("output_tokens", 0) or 0)

    by_tier = []
    grand_total_cost = 0.0
    grand_total_tokens = 0
    for tier in _TIERS:
        t = totals[tier]
        total_tokens = (
            t["input_tokens"] + t["cache_read_tokens"]
            + t["cache_creation_tokens"] + t["output_tokens"]
        )
        if total_tokens == 0:
            continue
        in_price, out_price = _PRICING[tier]
        cost = (
            t["input_tokens"] * in_price
            + t["cache_read_tokens"] * in_price * _CACHE_READ_MULT
            + t["cache_creation_tokens"] * in_price * _CACHE_WRITE_MULT
            + t["output_tokens"] * out_price
        ) / 1_000_000
        cost = round(cost, 4)
        grand_total_cost += cost
        grand_total_tokens += total_tokens
        by_tier.append({
            "tier": tier,
            "input_tokens": t["input_tokens"],
            "cache_read_tokens": t["cache_read_tokens"],
            "cache_creation_tokens": t["cache_creation_tokens"],
            "output_tokens": t["output_tokens"],
            "total_tokens": total_tokens,
            "cost_usd": cost,
        })

    return {
        "source": "transcripts",
        "project_dir": project_dir or "",
        "by_tier": by_tier,
        "total_tokens": grand_total_tokens,
        "total_cost_usd": round(grand_total_cost, 4),
    }


def _agent_type_for(jsonl_path: str) -> str:
    """Read the sibling <id>.meta.json to label a subagent transcript, else ''."""
    meta = jsonl_path[:-len(".jsonl")] + ".meta.json" if jsonl_path.endswith(".jsonl") else ""
    try:
        with open(meta) as fh:
            return json.load(fh).get("agentType", "") or ""
    except OSError:
        return ""
    except json.JSONDecodeError:
        return ""


def peak_context_by_subagent(project_dir=None) -> list:
    """
    For each subagent transcript, the PEAK single-request context size
    (input + cache_read + cache_creation tokens) — the largest working context
    the agent held at once. This is the right metric for a per-subtask context
    budget (vs. cumulative tokens, which double-counts cache reads).

    Returns [{agent_type, model, peak_context_tokens, file}], newest file first.
    """
    if project_dir is None:
        project_dir = find_active_project_dir()
    rows = []
    if not (project_dir and os.path.isdir(project_dir)):
        return rows
    sub_glob = os.path.join(project_dir, "**", "subagents", "agent-*.jsonl")

    def _safe_mtime(p):
        try:
            return os.path.getmtime(p)
        except OSError:
            return 0.0

    for path in sorted(glob.glob(sub_glob, recursive=True), key=lambda p: -_safe_mtime(p)):
        peak = 0
        model = ""
        for tier_unused, usage in _iter_assistant_usage(path):
            ctx = (
                int(usage.get("input_tokens", 0) or 0)
                + int(usage.get("cache_read_input_tokens", 0) or 0)
                + int(usage.get("cache_creation_input_tokens", 0) or 0)
            )
            peak = max(peak, ctx)
        # model: read first assistant record's model
        for line in _read_first_model(path):
            model = line
            break
        if peak > 0:
            rows.append({
                "agent_type": _agent_type_for(path),
                "model": model,
                "peak_context_tokens": peak,
                "file": os.path.basename(path),
            })
    return rows


def _read_first_model(jsonl_path: str):
    """Yield the model of the first assistant record (helper for labeling)."""
    try:
        with open(jsonl_path) as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = rec.get("message") or {}
                if rec.get("type") == "assistant" and isinstance(msg, dict) and msg.get("model"):
                    yield msg["model"]
                    return
    except OSError:
        return


def check_token_budget(limit_tokens: int = 170_000, project_dir=None) -> dict:
    """
    Check each subagent's peak context against a budget (default 170k tokens).
    Returns {limit, checked, over_budget: [...], ok: bool}.
    """
    rows = peak_context_by_subagent(project_dir)
    over = [r for r in rows if r["peak_context_tokens"] > limit_tokens]
    return {
        "limit_tokens": limit_tokens,
        "checked": len(rows),
        "over_budget": over,
        "ok": not over,
    }


def summarize_token_budget(limit_tokens: int = 170_000, project_dir=None) -> str:
    """Human-readable budget check for the CLI / MCP tool."""
    result = check_token_budget(limit_tokens, project_dir)
    if result["checked"] == 0:
        return "No subagent transcripts found to check."
    if result["ok"]:
        return f"✓ All {result['checked']} subagent(s) within the {limit_tokens:,}-token context budget."
    lines = [f"⚠ {len(result['over_budget'])} of {result['checked']} subagent(s) exceeded the {limit_tokens:,}-token context budget:"]
    for r in result["over_budget"]:
        label = r["agent_type"] or r["model"] or r["file"]
        lines.append(f"  {label}: peak context ~{r['peak_context_tokens']:,} tokens")
    return "\n".join(lines)


def summarize_real_usage(project_dir=None) -> str:
    """Human-readable summary of real token usage for the CLI / MCP tool."""
    data = aggregate_real_usage(project_dir)
    if not data["by_tier"]:
        return "No real token usage found (no Claude Code transcripts located)."
    lines = ["=== Real Token Usage (from transcripts) ==="]
    lines.append(f"  {'Tier':<8} {'In':>12} {'CacheRd':>12} {'CacheWr':>12} {'Out':>12} {'Cost':>10}")
    lines.append("  " + "-" * 70)
    for t in data["by_tier"]:
        lines.append(
            f"  {t['tier']:<8} {t['input_tokens']:>12,} {t['cache_read_tokens']:>12,} "
            f"{t['cache_creation_tokens']:>12,} {t['output_tokens']:>12,} ~${t['cost_usd']:>8.3f}"
        )
    lines.append("  " + "-" * 70)
    lines.append(
        f"  {'Total':<8} {'':>12} {'':>12} {'':>12} {data['total_tokens']:>12,} ~${data['total_cost_usd']:>8.3f}"
    )
    lines.append("  Real token counts from session transcripts; cost includes cache pricing.")
    return "\n".join(lines)


if __name__ == "__main__":
    print(summarize_real_usage())
