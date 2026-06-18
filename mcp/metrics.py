"""
Append-only JSONL metrics log for the Planner-Worker-Reviewer workflow.

Each record: {"ts": float, "phase": str, "model": str, "outcome": str, "meta": dict}

Phases written automatically:
  "ollama_call" — every ask_local_model call (latency, model, size)
  "workflow"    — one record per workflow run (outcome, retries, files)
"""
import json
import os
import time
from collections import defaultdict
from datetime import datetime, timezone

METRICS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "metrics.jsonl")


def _to_int(value) -> int:
    """Coerce a possibly-corrupt metrics value to int, defaulting to 0.

    Handles ints, floats, numeric strings ("2", "2.7"), and junk ("abc", None)
    without raising — corrupt records must never crash the metrics readers.
    """
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


# Estimated Claude API cost per agent call (rough averages for multi-model-flow agent sizes).
# Labelled "estimated" in the UI — actual costs depend on real token counts.
_CLAUDE_COST_PER_CALL = {
    "opus":   0.19,   # ~8k input + ~900 output @ $15/$75 per 1M tokens
    "fable":  0.08,   # ~8k input + ~600 output (estimated)
    "sonnet": 0.06,   # ~12k input + ~1.5k output @ $3/$15 per 1M tokens
    "haiku":  0.005,  # ~3k input + ~700 output @ $0.80/$4 per 1M tokens
}

# Estimated tokens per call (input + output) — derived from the cost assumptions above.
_CLAUDE_TOKENS_PER_CALL = {
    "opus":   8_900,   # ~8k input + ~900 output
    "fable":  8_600,   # ~8k input + ~600 output
    "sonnet": 13_500,  # ~12k input + ~1.5k output
    "haiku":  3_700,   # ~3k input + ~700 output
}


def append(record: dict) -> None:
    record.setdefault("ts", time.time())
    with open(METRICS_FILE, "a") as fh:
        fh.write(json.dumps(record) + "\n")


def read_all() -> list[dict]:
    try:
        with open(METRICS_FILE) as fh:
            records = []
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass  # skip lines truncated by a previous crash or disk-full
            return records
    except FileNotFoundError:
        return []


def summarize() -> str:
    records = read_all()
    if not records:
        return "No metrics recorded yet. Run a workflow or make an Ollama call first."

    workflow_records = [r for r in records if r.get("phase") == "workflow"]
    ollama_records = [r for r in records if r.get("phase") == "ollama_call"]
    lines: list[str] = []

    # ── Workflow runs ─────────────────────────────────────────────────────────
    if workflow_records:
        lines.append("=== Workflow Runs ===")
        outcome_counts: dict[str, int] = defaultdict(int)
        total_retries = 0
        for r in workflow_records:
            outcome_counts[r.get("outcome", "unknown")] += 1
            total_retries += _to_int(r.get("meta", {}).get("retries", 0))
        lines.append(f"Total:    {len(workflow_records)}")
        lines.append("Outcomes: " + "  ".join(f"{k}={v}" for k, v in sorted(outcome_counts.items())))
        lines.append(f"Avg retries per run: {total_retries / len(workflow_records):.1f}")
        lines.append("")
        lines.append("Recent runs (newest first):")
        for r in reversed(workflow_records[-10:]):
            ts = datetime.fromtimestamp(r["ts"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            meta = r.get("meta", {})
            outcome = r.get("outcome", "?")
            lines.append(
                f"  {ts}  {outcome:<22}  "
                f"steps={meta.get('steps_planned', '?')} "
                f"files={meta.get('files_written', '?')} "
                f"retries={meta.get('retries', 0)}"
            )
            task = str(meta.get("task", ""))[:60]
            if task:
                lines.append(f"    task: {task}")
        lines.append("")

    # ── Ollama calls ──────────────────────────────────────────────────────────
    if ollama_records:
        lines.append("=== Ollama Calls ===")
        lines.append(f"Total: {len(ollama_records)} calls")
        lines.append("")
        by_model: dict[str, list] = defaultdict(list)
        for r in ollama_records:
            by_model[r.get("model", "unknown")].append(r)
        lines.append(f"  {'Model':<30}  {'Calls':>5}  {'Avg latency':>12}  {'Errors':>6}")
        lines.append("  " + "-" * 58)
        for model, calls in sorted(by_model.items()):
            errors = sum(1 for c in calls if c.get("outcome") == "error")
            durations = [
                c["meta"]["duration_ms"]
                for c in calls
                if "meta" in c and "duration_ms" in c["meta"]
            ]
            avg_str = f"{sum(durations) / len(durations) / 1000:.1f}s" if durations else "n/a"
            lines.append(f"  {model:<30}  {len(calls):>5}  {avg_str:>12}  {errors:>6}")
        lines.append("")
        total_in = sum(r.get("meta", {}).get("prompt_chars", 0) for r in ollama_records)
        total_out = sum(r.get("meta", {}).get("response_chars", 0) for r in ollama_records)
        lines.append(f"Approx tokens in:  {total_in // 4:,}  (from {total_in:,} chars)")
        lines.append(f"Approx tokens out: {total_out // 4:,}  (from {total_out:,} chars)")
        lines.append("")
    # ── Claude call summary ────────────────────────────────────────────────────
    claude_totals: dict = {"opus": 0, "fable": 0, "sonnet": 0, "haiku": 0}
    for r in workflow_records:
        cc = r.get("meta", {}).get("claude_calls", {})
        for tier, count in cc.items():
            if tier in claude_totals:
                claude_totals[tier] += _to_int(count)

    if any(v > 0 for v in claude_totals.values()):
        lines.append("=== Claude API Calls (estimated) ===")
        total_claude_cost = 0.0
        for tier in ("opus", "fable", "sonnet", "haiku"):
            n = claude_totals[tier]
            if n == 0:
                continue
            cost = n * _CLAUDE_COST_PER_CALL.get(tier, 0)
            total_claude_cost += cost
            lines.append(f"  {tier:<10}  {n:>3} call(s)  ~${cost:.3f}")
        lines.append(f"  {'Total':<10}       ~${total_claude_cost:.3f}  (rough estimate)")
        lines.append("  Costs estimated from call counts × typical prompt sizes.")
        lines.append("")

    if not workflow_records and not ollama_records:
        lines.append("No workflow or Ollama events recorded yet.")

    return "\n".join(lines)


def aggregate() -> dict:
    """
    Aggregate metrics into a JSON-serializable dict for the UI/API.

    Returns a dict with 'workflow' and 'ollama' keys containing aggregated stats,
    token estimates, and recent run details. Safely handles missing meta fields
    using .get() to avoid crashes.
    """
    records = read_all()
    workflow_records = [r for r in records if r.get("phase") == "workflow"]
    ollama_records = [r for r in records if r.get("phase") == "ollama_call"]

    # ── Workflow aggregation ──────────────────────────────────────────────────
    workflow_section = {
        "total": len(workflow_records),
        "outcome_counts": {},
        "avg_retries": 0.0,
        "recent": [],
    }

    if workflow_records:
        outcome_counts: dict[str, int] = defaultdict(int)
        total_retries = 0
        for r in workflow_records:
            outcome_counts[r.get("outcome", "unknown")] += 1
            total_retries += _to_int(r.get("meta", {}).get("retries", 0))
        workflow_section["outcome_counts"] = dict(outcome_counts)
        workflow_section["avg_retries"] = total_retries / len(workflow_records)

        # Recent runs (newest first, last 10)
        for r in reversed(workflow_records[-10:]):
            meta = r.get("meta", {})
            recent_item = {
                "ts": r.get("ts", 0.0),
                "outcome": r.get("outcome", "unknown"),
                "steps_planned": meta.get("steps_planned"),
                "files_written": meta.get("files_written"),
                "retries": meta.get("retries", 0),
                "task": meta.get("task", ""),
            }
            workflow_section["recent"].append(recent_item)

    # ── Ollama aggregation ────────────────────────────────────────────────────
    ollama_section = {
        "total": len(ollama_records),
        "by_model": [],
        "approx_tokens_in": 0,
        "approx_tokens_out": 0,
    }

    if ollama_records:
        by_model: dict[str, list] = defaultdict(list)
        for r in ollama_records:
            by_model[r.get("model", "unknown")].append(r)

        # Per-model stats
        for model in sorted(by_model.keys()):
            calls = by_model[model]
            errors = sum(1 for c in calls if c.get("outcome") == "error")
            durations = [
                c["meta"]["duration_ms"]
                for c in calls
                if c.get("meta", {}).get("duration_ms") is not None
            ]
            avg_latency_ms = sum(durations) / len(durations) if durations else None
            model_item = {
                "model": model,
                "calls": len(calls),
                "avg_latency_ms": avg_latency_ms,
                "errors": errors,
            }
            ollama_section["by_model"].append(model_item)

        # Token estimates
        total_in = sum(r.get("meta", {}).get("prompt_chars", 0) for r in ollama_records)
        total_out = sum(r.get("meta", {}).get("response_chars", 0) for r in ollama_records)
        ollama_section["approx_tokens_in"] = total_in // 4
        ollama_section["approx_tokens_out"] = total_out // 4

    # ── Claude aggregation ────────────────────────────────────────────────────
    claude_totals: dict = {"opus": 0, "fable": 0, "sonnet": 0, "haiku": 0}
    for r in workflow_records:
        cc = r.get("meta", {}).get("claude_calls", {})
        for tier, count in cc.items():
            if tier in claude_totals:
                claude_totals[tier] += _to_int(count)

    claude_by_tier = []
    total_claude_cost = 0.0
    for tier in ("opus", "fable", "sonnet", "haiku"):
        n = claude_totals[tier]
        if n == 0:
            continue
        cost = round(n * _CLAUDE_COST_PER_CALL.get(tier, 0), 4)
        total_claude_cost += cost
        tokens = n * _CLAUDE_TOKENS_PER_CALL.get(tier, 0)
        claude_by_tier.append({"tier": tier, "calls": n, "est_tokens": tokens, "est_cost_usd": cost})

    ollama_call_count = sum(m["calls"] for m in ollama_section["by_model"])
    est_ollama_savings = round(ollama_call_count * _CLAUDE_COST_PER_CALL["haiku"], 4)

    total_calls = sum(claude_totals.values())
    est_all_opus = round(total_calls * _CLAUDE_COST_PER_CALL["opus"], 4)
    est_all_sonnet = round(total_calls * _CLAUDE_COST_PER_CALL["sonnet"], 4)

    return {
        "workflow": workflow_section,
        "ollama": ollama_section,
        "claude": {
            "total_calls": total_calls,
            "by_tier": claude_by_tier,
            "est_total_cost_usd": round(total_claude_cost, 4),
            "est_ollama_savings_usd": est_ollama_savings,
            "est_all_opus_cost_usd": est_all_opus,
            "est_all_sonnet_cost_usd": est_all_sonnet,
            "savings_vs_opus_usd": round(est_all_opus - total_claude_cost, 4),
            "savings_vs_sonnet_usd": round(est_all_sonnet - total_claude_cost, 4),
        },
    }


if __name__ == "__main__":
    print(summarize())
