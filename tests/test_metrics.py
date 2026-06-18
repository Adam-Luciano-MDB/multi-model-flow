"""Tests for mcp/metrics.py"""
import json
import time
from unittest.mock import patch

import metrics


class TestAppend:
    def test_writes_jsonl_record(self, tmp_path):
        f = str(tmp_path / "m.jsonl")
        with patch.object(metrics, "METRICS_FILE", f):
            metrics.append({"phase": "test", "model": "m", "outcome": "ok"})
        record = json.loads(open(f).read())
        assert record["phase"] == "test"
        assert record["model"] == "m"

    def test_adds_timestamp_when_missing(self, tmp_path):
        f = str(tmp_path / "m.jsonl")
        before = time.time()
        with patch.object(metrics, "METRICS_FILE", f):
            metrics.append({"phase": "x"})
        record = json.loads(open(f).read())
        assert "ts" in record
        assert record["ts"] >= before

    def test_preserves_existing_timestamp(self, tmp_path):
        f = str(tmp_path / "m.jsonl")
        with patch.object(metrics, "METRICS_FILE", f):
            metrics.append({"phase": "x", "ts": 1234567890.0})
        record = json.loads(open(f).read())
        assert record["ts"] == 1234567890.0

    def test_appends_multiple_records_one_per_line(self, tmp_path):
        f = str(tmp_path / "m.jsonl")
        with patch.object(metrics, "METRICS_FILE", f):
            metrics.append({"phase": "a"})
            metrics.append({"phase": "b"})
            metrics.append({"phase": "c"})
        lines = open(f).readlines()
        assert len(lines) == 3
        assert json.loads(lines[1])["phase"] == "b"


class TestReadAll:
    def test_returns_empty_list_when_file_missing(self, tmp_path):
        f = str(tmp_path / "nonexistent.jsonl")
        with patch.object(metrics, "METRICS_FILE", f):
            assert metrics.read_all() == []

    def test_returns_all_records(self, tmp_path):
        f = str(tmp_path / "m.jsonl")
        with open(f, "w") as fh:
            fh.write(json.dumps({"phase": "a", "ts": 1.0}) + "\n")
            fh.write(json.dumps({"phase": "b", "ts": 2.0}) + "\n")
        with patch.object(metrics, "METRICS_FILE", f):
            records = metrics.read_all()
        assert len(records) == 2
        assert records[0]["phase"] == "a"
        assert records[1]["phase"] == "b"

    def test_skips_blank_lines(self, tmp_path):
        f = str(tmp_path / "m.jsonl")
        with open(f, "w") as fh:
            fh.write(json.dumps({"phase": "a"}) + "\n")
            fh.write("\n")
            fh.write("   \n")
            fh.write(json.dumps({"phase": "b"}) + "\n")
        with patch.object(metrics, "METRICS_FILE", f):
            records = metrics.read_all()
        assert len(records) == 2

    def test_skips_malformed_json_lines(self, tmp_path):
        f = str(tmp_path / "m.jsonl")
        with open(f, "w") as fh:
            fh.write(json.dumps({"phase": "a", "ts": 1.0}) + "\n")
            fh.write("THIS IS NOT JSON\n")  # truncated write from prior crash
            fh.write(json.dumps({"phase": "b", "ts": 2.0}) + "\n")
        with patch.object(metrics, "METRICS_FILE", f):
            records = metrics.read_all()
        assert len(records) == 2
        assert records[0]["phase"] == "a"
        assert records[1]["phase"] == "b"


class TestSummarize:
    def test_returns_no_metrics_message_when_empty(self, tmp_path):
        f = str(tmp_path / "m.jsonl")
        with patch.object(metrics, "METRICS_FILE", f):
            result = metrics.summarize()
        assert "No metrics" in result

    def test_workflow_section_shows_totals_and_task(self, tmp_path):
        f = str(tmp_path / "m.jsonl")
        with open(f, "w") as fh:
            fh.write(json.dumps({
                "ts": 1750000000.0,
                "phase": "workflow",
                "model": "opus+haiku+sonnet",
                "outcome": "approved",
                "meta": {"task": "Add pagination", "steps_planned": 3, "files_written": 3, "retries": 0},
            }) + "\n")
        with patch.object(metrics, "METRICS_FILE", f):
            result = metrics.summarize()
        assert "Workflow Runs" in result
        assert "approved=1" in result
        assert "Add pagination" in result

    def test_outcome_counts_aggregated_correctly(self, tmp_path):
        f = str(tmp_path / "m.jsonl")
        with open(f, "w") as fh:
            for outcome in ["approved", "approved", "rejected", "approved_with_notes"]:
                fh.write(json.dumps({
                    "ts": 1750000000.0, "phase": "workflow",
                    "model": "m", "outcome": outcome, "meta": {"retries": 0},
                }) + "\n")
        with patch.object(metrics, "METRICS_FILE", f):
            result = metrics.summarize()
        assert "approved=2" in result
        assert "rejected=1" in result
        assert "approved_with_notes=1" in result

    def test_avg_retries_calculated(self, tmp_path):
        f = str(tmp_path / "m.jsonl")
        with open(f, "w") as fh:
            for retries in [0, 2]:
                fh.write(json.dumps({
                    "ts": 1750000000.0, "phase": "workflow",
                    "model": "m", "outcome": "approved", "meta": {"retries": retries},
                }) + "\n")
        with patch.object(metrics, "METRICS_FILE", f):
            result = metrics.summarize()
        assert "1.0" in result  # avg of 0 and 2

    def test_ollama_section_shows_model_and_latency(self, tmp_path):
        f = str(tmp_path / "m.jsonl")
        with open(f, "w") as fh:
            fh.write(json.dumps({
                "ts": 1750000000.0,
                "phase": "ollama_call",
                "model": "qwen2.5-coder:7b",
                "outcome": "success",
                "meta": {"prompt_chars": 400, "response_chars": 100, "duration_ms": 5000},
            }) + "\n")
        with patch.object(metrics, "METRICS_FILE", f):
            result = metrics.summarize()
        assert "Ollama Calls" in result
        assert "qwen2.5-coder:7b" in result
        assert "5.0s" in result

    def test_token_estimates_from_char_counts(self, tmp_path):
        f = str(tmp_path / "m.jsonl")
        with open(f, "w") as fh:
            fh.write(json.dumps({
                "ts": 1750000000.0,
                "phase": "ollama_call",
                "model": "qwen2.5-coder:7b",
                "outcome": "success",
                "meta": {"prompt_chars": 4000, "response_chars": 400, "duration_ms": 1000},
            }) + "\n")
        with patch.object(metrics, "METRICS_FILE", f):
            result = metrics.summarize()
        assert "1,000" in result   # 4000 // 4 tokens in
        assert "100" in result     # 400 // 4 tokens out

    def test_records_with_missing_meta_do_not_crash(self, tmp_path):
        f = str(tmp_path / "m.jsonl")
        with open(f, "w") as fh:
            fh.write(json.dumps({"ts": 1750000000.0, "phase": "workflow", "outcome": "approved"}) + "\n")
            fh.write(json.dumps({"ts": 1750000000.0, "phase": "ollama_call", "outcome": "success"}) + "\n")
        with patch.object(metrics, "METRICS_FILE", f):
            result = metrics.summarize()
        assert isinstance(result, str)


class TestAggregate:
    def test_empty_file_returns_zero_totals(self, tmp_path):
        f = str(tmp_path / "m.jsonl")
        with patch.object(metrics, "METRICS_FILE", f):
            result = metrics.aggregate()
        assert isinstance(result, dict)
        assert result["workflow"]["total"] == 0
        assert result["workflow"]["outcome_counts"] == {}
        assert result["workflow"]["avg_retries"] == 0.0
        assert result["workflow"]["recent"] == []
        assert result["ollama"]["total"] == 0
        assert result["ollama"]["by_model"] == []
        assert result["ollama"]["approx_tokens_in"] == 0
        assert result["ollama"]["approx_tokens_out"] == 0

    def test_mixed_workflow_outcomes_and_avg_retries(self, tmp_path):
        f = str(tmp_path / "m.jsonl")
        with open(f, "w") as fh:
            for outcome, retries in [("approved", 0), ("approved", 2), ("rejected", 1)]:
                fh.write(json.dumps({
                    "ts": 1750000000.0, "phase": "workflow",
                    "model": "m", "outcome": outcome, "meta": {"retries": retries, "task": "test"},
                }) + "\n")
        with patch.object(metrics, "METRICS_FILE", f):
            result = metrics.aggregate()
        assert result["workflow"]["total"] == 3
        assert result["workflow"]["outcome_counts"] == {"approved": 2, "rejected": 1}
        assert result["workflow"]["avg_retries"] == (0 + 2 + 1) / 3
        assert len(result["workflow"]["recent"]) == 3

    def test_ollama_records_by_model_stats(self, tmp_path):
        f = str(tmp_path / "m.jsonl")
        with open(f, "w") as fh:
            fh.write(json.dumps({
                "ts": 1750000000.0,
                "phase": "ollama_call",
                "model": "qwen2.5-coder:7b",
                "outcome": "success",
                "meta": {"prompt_chars": 4000, "response_chars": 400, "duration_ms": 5000},
            }) + "\n")
            fh.write(json.dumps({
                "ts": 1750000001.0,
                "phase": "ollama_call",
                "model": "qwen2.5-coder:7b",
                "outcome": "success",
                "meta": {"prompt_chars": 2000, "response_chars": 200, "duration_ms": 3000},
            }) + "\n")
        with patch.object(metrics, "METRICS_FILE", f):
            result = metrics.aggregate()
        assert result["ollama"]["total"] == 2
        assert len(result["ollama"]["by_model"]) == 1
        model_entry = result["ollama"]["by_model"][0]
        assert model_entry["model"] == "qwen2.5-coder:7b"
        assert model_entry["calls"] == 2
        assert model_entry["avg_latency_ms"] == (5000 + 3000) / 2
        assert model_entry["errors"] == 0
        assert result["ollama"]["approx_tokens_in"] == (4000 + 2000) // 4
        assert result["ollama"]["approx_tokens_out"] == (400 + 200) // 4

    def test_workflow_and_ollama_records_with_missing_meta_do_not_crash(self, tmp_path):
        f = str(tmp_path / "m.jsonl")
        with open(f, "w") as fh:
            fh.write(json.dumps({"ts": 1750000000.0, "phase": "workflow", "outcome": "approved"}) + "\n")
            fh.write(json.dumps({"ts": 1750000001.0, "phase": "ollama_call", "outcome": "success"}) + "\n")
        with patch.object(metrics, "METRICS_FILE", f):
            result = metrics.aggregate()
        assert isinstance(result, dict)
        assert result["workflow"]["total"] == 1
        assert result["workflow"]["outcome_counts"] == {"approved": 1}
        assert result["workflow"]["avg_retries"] == 0.0
        assert result["ollama"]["total"] == 1
        assert len(result["ollama"]["by_model"]) == 1
        assert result["ollama"]["approx_tokens_in"] == 0
        assert result["ollama"]["approx_tokens_out"] == 0

    def test_ollama_record_missing_duration_ms_excluded_from_avg(self, tmp_path):
        f = str(tmp_path / "m.jsonl")
        with open(f, "w") as fh:
            fh.write(json.dumps({
                "ts": 1.0, "phase": "ollama_call", "model": "m", "outcome": "success",
                "meta": {"duration_ms": 4000, "prompt_chars": 100, "response_chars": 50},
            }) + "\n")
            fh.write(json.dumps({
                "ts": 2.0, "phase": "ollama_call", "model": "m", "outcome": "error",
                "meta": {},  # no duration_ms
            }) + "\n")
        with patch.object(metrics, "METRICS_FILE", f):
            result = metrics.aggregate()
        entry = result["ollama"]["by_model"][0]
        assert entry["avg_latency_ms"] == 4000.0  # only the one with duration_ms counts
        assert entry["errors"] == 1

    def test_retries_as_numeric_string_is_parsed(self, tmp_path):
        f = str(tmp_path / "m.jsonl")
        with open(f, "w") as fh:
            fh.write(json.dumps({
                "ts": 1.0, "phase": "workflow", "outcome": "approved",
                "meta": {"retries": "2"},  # stored as string (corrupt record)
            }) + "\n")
        with patch.object(metrics, "METRICS_FILE", f):
            result = metrics.aggregate()
        assert result["workflow"]["avg_retries"] == 2.0

    def test_retries_as_junk_string_does_not_crash(self, tmp_path):
        f = str(tmp_path / "m.jsonl")
        with open(f, "w") as fh:
            fh.write(json.dumps({
                "ts": 1.0, "phase": "workflow", "outcome": "approved",
                "meta": {"retries": "abc"},  # non-numeric junk
            }) + "\n")
            fh.write(json.dumps({
                "ts": 2.0, "phase": "workflow", "outcome": "approved",
                "meta": {"retries": "2.7"},  # float-valued string
            }) + "\n")
        with patch.object(metrics, "METRICS_FILE", f):
            agg = metrics.aggregate()
            summary = metrics.summarize()
        # "abc" → 0, "2.7" → 2; avg over 2 runs = 1.0. Neither readers crash.
        assert agg["workflow"]["avg_retries"] == 1.0
        assert isinstance(summary, str)

    def test_aggregate_includes_claude_key(self, tmp_path):
        f = str(tmp_path / "m.jsonl")
        with patch.object(metrics, "METRICS_FILE", f):
            result = metrics.aggregate()
        assert "claude" in result
        assert result["claude"]["total_calls"] == 0
        assert result["claude"]["by_tier"] == []
        assert result["claude"]["est_total_cost_usd"] == 0.0
        assert result["claude"]["est_ollama_savings_usd"] == 0.0

    def test_aggregate_claude_calls_summed_from_workflow_meta(self, tmp_path):
        f = str(tmp_path / "m.jsonl")
        with open(f, "w") as fh:
            fh.write(json.dumps({
                "ts": 1.0, "phase": "workflow", "outcome": "approved",
                "meta": {"retries": 0, "claude_calls": {"opus": 1, "haiku": 5, "sonnet": 1, "fable": 0}},
            }) + "\n")
            fh.write(json.dumps({
                "ts": 2.0, "phase": "workflow", "outcome": "approved",
                "meta": {"retries": 0, "claude_calls": {"opus": 1, "haiku": 4, "sonnet": 1, "fable": 0}},
            }) + "\n")
        with patch.object(metrics, "METRICS_FILE", f):
            result = metrics.aggregate()
        assert result["claude"]["total_calls"] == 13  # (1+5+1) + (1+4+1)
        tiers = {t["tier"]: t for t in result["claude"]["by_tier"]}
        assert tiers["opus"]["calls"] == 2
        assert tiers["haiku"]["calls"] == 9
        assert tiers["sonnet"]["calls"] == 2
        assert "fable" not in tiers  # zero-count tiers omitted

    def test_aggregate_claude_backward_compat_missing_claude_calls(self, tmp_path):
        # Old workflow records without claude_calls must not crash and contribute 0
        f = str(tmp_path / "m.jsonl")
        with open(f, "w") as fh:
            fh.write(json.dumps({
                "ts": 1.0, "phase": "workflow", "outcome": "approved",
                "meta": {"retries": 0, "task": "old run"},  # no claude_calls key
            }) + "\n")
        with patch.object(metrics, "METRICS_FILE", f):
            result = metrics.aggregate()
        assert result["claude"]["total_calls"] == 0
        assert result["claude"]["est_total_cost_usd"] == 0.0

    def test_aggregate_est_ollama_savings_from_ollama_calls(self, tmp_path):
        f = str(tmp_path / "m.jsonl")
        with open(f, "w") as fh:
            for _ in range(4):
                fh.write(json.dumps({
                    "ts": 1.0, "phase": "ollama_call", "model": "devstral",
                    "outcome": "success",
                    "meta": {"prompt_chars": 400, "response_chars": 200, "duration_ms": 1000},
                }) + "\n")
        with patch.object(metrics, "METRICS_FILE", f):
            result = metrics.aggregate()
        # 4 ollama calls × haiku cost per call = savings
        expected = round(4 * metrics._CLAUDE_COST_PER_CALL["haiku"], 4)
        assert result["claude"]["est_ollama_savings_usd"] == expected

    def test_aggregate_claude_cost_estimated_from_call_counts(self, tmp_path):
        f = str(tmp_path / "m.jsonl")
        with open(f, "w") as fh:
            fh.write(json.dumps({
                "ts": 1.0, "phase": "workflow", "outcome": "approved",
                "meta": {"retries": 0, "claude_calls": {"opus": 2, "sonnet": 1, "haiku": 6, "fable": 0}},
            }) + "\n")
        with patch.object(metrics, "METRICS_FILE", f):
            result = metrics.aggregate()
        c = result["claude"]
        expected_cost = round(
            2 * metrics._CLAUDE_COST_PER_CALL["opus"] +
            1 * metrics._CLAUDE_COST_PER_CALL["sonnet"] +
            6 * metrics._CLAUDE_COST_PER_CALL["haiku"],
            4,
        )
        assert c["est_total_cost_usd"] == expected_cost

    def test_aggregate_claude_includes_cost_comparison_fields(self, tmp_path):
        f = str(tmp_path / "m.jsonl")
        with open(f, "w") as fh:
            fh.write(json.dumps({
                "ts": 1.0, "phase": "workflow", "outcome": "approved",
                "meta": {"retries": 0, "claude_calls": {"opus": 1, "sonnet": 1, "haiku": 4, "fable": 0}},
            }) + "\n")
        with patch.object(metrics, "METRICS_FILE", f):
            result = metrics.aggregate()
        c = result["claude"]
        total_calls = 6  # 1+1+4
        assert c["est_all_opus_cost_usd"] == round(total_calls * metrics._CLAUDE_COST_PER_CALL["opus"], 4)
        assert c["est_all_sonnet_cost_usd"] == round(total_calls * metrics._CLAUDE_COST_PER_CALL["sonnet"], 4)
        assert c["savings_vs_opus_usd"] == round(c["est_all_opus_cost_usd"] - c["est_total_cost_usd"], 4)
        assert c["savings_vs_sonnet_usd"] == round(c["est_all_sonnet_cost_usd"] - c["est_total_cost_usd"], 4)
        # Actual mixed cost must be less than all-Opus cost
        assert c["est_total_cost_usd"] < c["est_all_opus_cost_usd"]

    def test_recent_runs_limited_to_last_10(self, tmp_path):
        f = str(tmp_path / "m.jsonl")
        with open(f, "w") as fh:
            for i in range(15):
                fh.write(json.dumps({
                    "ts": 1750000000.0 + i,
                    "phase": "workflow",
                    "outcome": "approved",
                    "meta": {"retries": 0, "task": f"task-{i}"},
                }) + "\n")
        with patch.object(metrics, "METRICS_FILE", f):
            result = metrics.aggregate()
        assert result["workflow"]["total"] == 15
        assert len(result["workflow"]["recent"]) == 10
        # Recent list is in reverse chronological order (newest first)
        assert result["workflow"]["recent"][0]["task"] == "task-14"
        assert result["workflow"]["recent"][-1]["task"] == "task-5"
