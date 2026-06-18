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
            total_retries += int(r.get("meta", {}).get("retries", 0) or 0)
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
        lines.append(
            "Note: Anthropic API calls (Opus/Sonnet/Haiku) are not tracked here.\n"
            "Use the Claude Console for cost reporting on those tiers."
        )

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
            total_retries += int(r.get("meta", {}).get("retries", 0) or 0)
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

    return {
        "workflow": workflow_section,
        "ollama": ollama_section,
    }


if __name__ == "__main__":
    print(summarize())
