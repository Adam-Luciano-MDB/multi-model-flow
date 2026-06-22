"""Tests for mcp/token_usage.py"""
import json

import token_usage


class TestModelToTier:
    def test_maps_known_tiers(self):
        assert token_usage.model_to_tier("claude-opus-4-8") == "opus"
        assert token_usage.model_to_tier("claude-sonnet-4-6") == "sonnet"
        assert token_usage.model_to_tier("claude-haiku-4-5-20251001") == "haiku"
        assert token_usage.model_to_tier("claude-fable-5") == "fable"

    def test_unknown_returns_empty(self):
        assert token_usage.model_to_tier("gpt-4") == ""
        assert token_usage.model_to_tier("") == ""


def _write_transcript(path, records):
    with open(path, "w") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")


class TestAggregateRealUsage:
    def test_empty_when_no_dir(self, tmp_path):
        result = token_usage.aggregate_real_usage(str(tmp_path / "nope"))
        assert result["by_tier"] == []
        assert result["total_tokens"] == 0
        assert result["total_cost_usd"] == 0

    def test_sums_usage_by_tier(self, tmp_path):
        _write_transcript(str(tmp_path / "session.jsonl"), [
            {"type": "assistant", "message": {"model": "claude-sonnet-4-6",
                "usage": {"input_tokens": 100, "output_tokens": 50,
                          "cache_read_input_tokens": 200, "cache_creation_input_tokens": 0}}},
            {"type": "user", "message": {"content": "ignored"}},
            {"type": "assistant", "message": {"model": "claude-sonnet-4-6",
                "usage": {"input_tokens": 100, "output_tokens": 50,
                          "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}}},
        ])
        result = token_usage.aggregate_real_usage(str(tmp_path))
        assert len(result["by_tier"]) == 1
        sonnet = result["by_tier"][0]
        assert sonnet["tier"] == "sonnet"
        assert sonnet["input_tokens"] == 200
        assert sonnet["output_tokens"] == 100
        assert sonnet["cache_read_tokens"] == 200
        assert sonnet["total_tokens"] == 500

    def test_cost_includes_cache_multipliers(self, tmp_path):
        _write_transcript(str(tmp_path / "s.jsonl"), [
            {"type": "assistant", "message": {"model": "claude-opus-4-8",
                "usage": {"input_tokens": 1_000_000, "output_tokens": 1_000_000,
                          "cache_read_input_tokens": 1_000_000,
                          "cache_creation_input_tokens": 1_000_000}}},
        ])
        result = token_usage.aggregate_real_usage(str(tmp_path))
        opus = result["by_tier"][0]
        # opus: $5 in / $25 out; cache read 0.1x=$0.5, cache write 1.25x=$6.25
        expected = 5.0 + 0.5 + 6.25 + 25.0
        assert opus["cost_usd"] == round(expected, 4)

    def test_includes_subagent_transcripts(self, tmp_path):
        _write_transcript(str(tmp_path / "main.jsonl"), [
            {"type": "assistant", "message": {"model": "claude-opus-4-8",
                "usage": {"input_tokens": 10, "output_tokens": 5}}},
        ])
        sub = tmp_path / "main" / "subagents"
        sub.mkdir(parents=True)
        _write_transcript(str(sub / "agent-x.jsonl"), [
            {"type": "assistant", "message": {"model": "claude-haiku-4-5",
                "usage": {"input_tokens": 20, "output_tokens": 7}}},
        ])
        result = token_usage.aggregate_real_usage(str(tmp_path))
        tiers = {t["tier"] for t in result["by_tier"]}
        assert tiers == {"opus", "haiku"}

    def test_skips_malformed_and_non_assistant(self, tmp_path):
        p = str(tmp_path / "s.jsonl")
        with open(p, "w") as fh:
            fh.write("not json\n")
            fh.write(json.dumps({"type": "user"}) + "\n")
            fh.write(json.dumps({"type": "assistant", "message": {"model": "claude-opus-4-8",
                "usage": {"input_tokens": 5, "output_tokens": 1}}}) + "\n")
        result = token_usage.aggregate_real_usage(str(tmp_path))
        assert result["total_tokens"] == 6


class TestTokenBudget:
    def _make_subagent(self, tmp_path, agent_type, peak_input):
        proj = tmp_path / "proj"
        sub = proj / "sess" / "subagents"
        sub.mkdir(parents=True, exist_ok=True)
        jsonl = sub / f"agent-{agent_type}.jsonl"
        with open(jsonl, "w") as fh:
            # two records; the larger input is the peak
            fh.write(json.dumps({"type": "assistant", "message": {"model": "claude-opus-4-8",
                "usage": {"input_tokens": 1000, "cache_read_input_tokens": 0, "output_tokens": 50}}}) + "\n")
            fh.write(json.dumps({"type": "assistant", "message": {"model": "claude-opus-4-8",
                "usage": {"input_tokens": peak_input, "cache_read_input_tokens": 0, "output_tokens": 50}}}) + "\n")
        with open(str(jsonl)[:-len(".jsonl")] + ".meta.json", "w") as fh:
            json.dump({"agentType": agent_type}, fh)
        return str(proj)

    def test_peak_context_uses_max_not_sum(self, tmp_path):
        proj = self._make_subagent(tmp_path, "planner", 50000)
        rows = token_usage.peak_context_by_subagent(proj)
        assert len(rows) == 1
        assert rows[0]["agent_type"] == "planner"
        assert rows[0]["peak_context_tokens"] == 50000  # max(1000, 50000), not the sum

    def test_check_flags_over_budget(self, tmp_path):
        proj = self._make_subagent(tmp_path, "planner", 200000)
        result = token_usage.check_token_budget(170000, proj)
        assert result["ok"] is False
        assert result["over_budget"][0]["agent_type"] == "planner"

    def test_check_ok_when_under(self, tmp_path):
        proj = self._make_subagent(tmp_path, "reviewer", 80000)
        result = token_usage.check_token_budget(170000, proj)
        assert result["ok"] is True
        assert result["over_budget"] == []

    def test_summary_messages(self, tmp_path):
        proj = self._make_subagent(tmp_path, "planner", 200000)
        out = token_usage.summarize_token_budget(170000, proj)
        assert "exceeded" in out and "planner" in out

    def test_no_subagents(self, tmp_path):
        out = token_usage.summarize_token_budget(170000, str(tmp_path / "empty"))
        assert "No subagent transcripts" in out


class TestSummarizeRealUsage:
    def test_message_when_empty(self, tmp_path):
        out = token_usage.summarize_real_usage(str(tmp_path / "nope"))
        assert "No real token usage" in out

    def test_includes_tiers_and_total(self, tmp_path):
        _write_transcript(str(tmp_path / "s.jsonl"), [
            {"type": "assistant", "message": {"model": "claude-opus-4-8",
                "usage": {"input_tokens": 100, "output_tokens": 50}}},
        ])
        out = token_usage.summarize_real_usage(str(tmp_path))
        assert "Real Token Usage" in out
        assert "opus" in out
        assert "Total" in out
