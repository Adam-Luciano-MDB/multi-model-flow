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
