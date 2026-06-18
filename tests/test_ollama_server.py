"""Tests for mcp/ollama_mcp_server.py

All tests mock httpx and the metrics module so they run fully offline with no
Ollama model required.
"""
import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

# metrics is imported first so we get the same module object the server uses
import metrics as _metrics_mod
import ollama_mcp_server as server


class TestAppendMetricSafe:
    """_append_metric must never propagate exceptions to callers."""

    def test_suppresses_write_exceptions(self):
        with patch.object(_metrics_mod, "append", side_effect=OSError("disk full")):
            server._append_metric({"phase": "test"})  # must not raise


class TestTotalRamGb:
    def test_returns_float_or_none(self):
        result = server._total_ram_gb()
        assert result is None or isinstance(result, float)

    def test_returns_none_when_sysconf_raises(self):
        with patch("os.sysconf", side_effect=OSError("unsupported")):
            assert server._total_ram_gb() is None


class TestListLocalModels:
    def test_error_string_when_offline(self):
        with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
            result = server.list_local_models()
        assert len(result) == 1
        assert result[0].startswith("ERROR")

    def test_returns_model_names_when_online(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "models": [{"name": "qwen2.5-coder:7b"}, {"name": "devstral:latest"}]
        }
        with patch("httpx.get", return_value=mock_resp):
            result = server.list_local_models()
        assert result == ["qwen2.5-coder:7b", "devstral:latest"]

    def test_error_string_on_unexpected_exception(self):
        with patch("httpx.get", side_effect=RuntimeError("boom")):
            result = server.list_local_models()
        assert result[0].startswith("ERROR")


class TestRecommendModel:
    def test_always_returns_a_string(self):
        result = server.recommend_model()
        assert isinstance(result, str) and len(result) > 0

    def test_fallback_message_when_ram_undetectable(self):
        with patch.object(server, "_total_ram_gb", return_value=None):
            result = server.recommend_model()
        assert "Could not detect" in result

    def test_recommendation_includes_model_name(self):
        with patch.object(server, "_total_ram_gb", return_value=64.0):
            with patch.object(server, "list_local_models", return_value=["ERROR: offline"]):
                result = server.recommend_model()
        assert "qwen2.5-coder:32b" in result

    def test_tells_user_to_pull_when_model_not_installed(self):
        with patch.object(server, "_total_ram_gb", return_value=16.0):
            with patch.object(server, "list_local_models", return_value=[]):
                result = server.recommend_model()
        assert "ollama pull" in result

    def test_reports_already_installed_when_model_present(self):
        with patch.object(server, "_total_ram_gb", return_value=64.0):
            with patch.object(server, "list_local_models", return_value=["qwen2.5-coder:32b"]):
                result = server.recommend_model()
        assert "installed already" in result
        assert "ollama pull" not in result


class TestAskLocalModel:
    def test_returns_error_string_not_exception_when_offline(self):
        with patch("httpx.post", side_effect=httpx.ConnectError("refused")):
            with patch.object(_metrics_mod, "append"):
                result = server.ask_local_model("qwen2.5-coder:7b", "hello")
        assert result.startswith("ERROR")
        assert "ollama serve" in result

    def test_returns_error_string_on_timeout(self):
        with patch("httpx.post", side_effect=httpx.TimeoutException("timed out")):
            with patch.object(_metrics_mod, "append"):
                result = server.ask_local_model("qwen2.5-coder:7b", "hello")
        assert result.startswith("ERROR")
        assert "timed out" in result.lower()

    def test_logs_error_metric_when_offline(self):
        logged = []
        with patch("httpx.post", side_effect=httpx.ConnectError("refused")):
            with patch.object(_metrics_mod, "append", side_effect=logged.append):
                server.ask_local_model("qwen2.5-coder:7b", "hello")
        assert len(logged) == 1
        assert logged[0]["phase"] == "ollama_call"
        assert logged[0]["outcome"] == "error"

    def test_logs_success_metric_with_correct_sizes(self):
        logged = []
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"response": "def foo(): pass"}
        with patch("httpx.post", return_value=mock_resp):
            with patch.object(_metrics_mod, "append", side_effect=logged.append):
                result = server.ask_local_model("qwen2.5-coder:7b", "write a function")
        assert result == "def foo(): pass"
        assert logged[0]["outcome"] == "success"
        assert logged[0]["meta"]["prompt_chars"] == len("write a function")
        assert logged[0]["meta"]["response_chars"] == len("def foo(): pass")
        assert logged[0]["meta"]["duration_ms"] >= 0

    def test_returns_error_string_on_http_status_error(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception("403 Forbidden")
        with patch("httpx.post", return_value=mock_resp):
            with patch.object(_metrics_mod, "append"):
                result = server.ask_local_model("qwen2.5-coder:7b", "hello")
        assert result.startswith("ERROR")

    def test_includes_system_prompt_in_payload(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"response": "ok"}
        with patch("httpx.post", return_value=mock_resp) as mock_post:
            with patch.object(_metrics_mod, "append"):
                server.ask_local_model("qwen2.5-coder:7b", "prompt", system="Be concise.")
        payload = mock_post.call_args.kwargs["json"]
        assert payload["system"] == "Be concise."

    def test_uses_default_model_when_model_arg_is_empty(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"response": "ok"}
        with patch("httpx.post", return_value=mock_resp) as mock_post:
            with patch.object(_metrics_mod, "append"):
                server.ask_local_model("", "prompt")
        payload = mock_post.call_args.kwargs["json"]
        assert payload["model"] == server.DEFAULT_MODEL


class TestAskLocalModelForCode:
    def test_returns_string_on_success(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"response": "def hello(): pass"}
        with patch("httpx.get", return_value=MagicMock(json=lambda: {"models": [{"name": "qwen2.5-coder:7b"}]})):
            with patch("httpx.post", return_value=mock_resp):
                with patch.object(_metrics_mod, "append"):
                    result = server.ask_local_model_for_code("write a hello function", language="Python")
        assert result == "def hello(): pass"

    def test_prefers_devstral_when_available(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"response": "ok"}
        with patch("httpx.get", return_value=MagicMock(json=lambda: {
            "models": [{"name": "qwen2.5-coder:7b"}, {"name": "devstral:latest"}]
        })):
            with patch("httpx.post", return_value=mock_resp) as mock_post:
                with patch.object(_metrics_mod, "append"):
                    server.ask_local_model_for_code("prompt")
        payload = mock_post.call_args.kwargs["json"]
        assert payload["model"] == "devstral:latest"

    def test_falls_back_to_default_model_when_no_models_available(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"response": "ok"}
        with patch("httpx.get", side_effect=Exception("offline")):
            with patch("httpx.post", return_value=mock_resp) as mock_post:
                with patch.object(_metrics_mod, "append"):
                    server.ask_local_model_for_code("prompt")
        payload = mock_post.call_args.kwargs["json"]
        assert payload["model"] == server.DEFAULT_MODEL

    def test_prepends_context_to_prompt(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"response": "ok"}
        with patch("httpx.get", return_value=MagicMock(json=lambda: {"models": [{"name": "qwen2.5-coder:7b"}]})):
            with patch("httpx.post", return_value=mock_resp) as mock_post:
                with patch.object(_metrics_mod, "append"):
                    server.ask_local_model_for_code("add a method", context="class Foo: pass")
        payload = mock_post.call_args.kwargs["json"]
        assert "Context (existing code):" in payload["prompt"]
        assert "class Foo: pass" in payload["prompt"]
        assert "add a method" in payload["prompt"]

    def test_language_hint_appears_in_system_prompt(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"response": "ok"}
        with patch("httpx.get", return_value=MagicMock(json=lambda: {"models": [{"name": "qwen2.5-coder:7b"}]})):
            with patch("httpx.post", return_value=mock_resp) as mock_post:
                with patch.object(_metrics_mod, "append"):
                    server.ask_local_model_for_code("prompt", language="Go")
        payload = mock_post.call_args.kwargs["json"]
        assert "Go" in payload["system"]

    def test_respects_explicit_model_argument(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"response": "ok"}
        with patch("httpx.get") as mock_get:
            with patch("httpx.post", return_value=mock_resp) as mock_post:
                with patch.object(_metrics_mod, "append"):
                    server.ask_local_model_for_code("prompt", model="custom:model")
        payload = mock_post.call_args.kwargs["json"]
        assert payload["model"] == "custom:model"
        mock_get.assert_not_called()  # list_local_models should be skipped

    def test_returns_error_string_when_ollama_offline(self):
        with patch("httpx.get", side_effect=Exception("offline")):
            with patch("httpx.post", side_effect=httpx.ConnectError("refused")):
                with patch.object(_metrics_mod, "append"):
                    result = server.ask_local_model_for_code("prompt")
        assert result.startswith("ERROR")


class TestTimeoutDefault:
    def test_default_timeout_is_1500_seconds(self):
        assert server.TIMEOUT == 1500


class TestLogEvent:
    def test_writes_parseable_record_to_metrics(self, tmp_path):
        f = str(tmp_path / "m.jsonl")
        with patch.object(_metrics_mod, "METRICS_FILE", f):
            server.log_event("workflow", "opus+haiku+sonnet", "approved", '{"retries": 1}')
        record = json.loads(open(f).read())
        assert record["phase"] == "workflow"
        assert record["outcome"] == "approved"
        assert record["meta"]["retries"] == 1

    def test_handles_invalid_metadata_json_gracefully(self, tmp_path):
        f = str(tmp_path / "m.jsonl")
        with patch.object(_metrics_mod, "METRICS_FILE", f):
            result = server.log_event("plan", "opus", "success", "not-valid-json{{")
        assert "Logged" in result
        record = json.loads(open(f).read())
        assert "raw" in record["meta"]

    def test_returns_confirmation_string(self):
        with patch.object(_metrics_mod, "append"):
            result = server.log_event("review", "sonnet", "rejected")
        assert "Logged" in result
        assert "review" in result

    def test_empty_metadata_json_defaults_to_empty_dict(self, tmp_path):
        f = str(tmp_path / "m.jsonl")
        with patch.object(_metrics_mod, "METRICS_FILE", f):
            server.log_event("plan", "opus", "success")  # no metadata_json arg
        record = json.loads(open(f).read())
        assert record["meta"] == {}


class TestGetMetricsSummary:
    def test_returns_a_string(self):
        with patch.object(_metrics_mod, "read_all", return_value=[]):
            result = server.get_metrics_summary()
        assert isinstance(result, str)

    def test_delegates_to_metrics_summarize(self):
        with patch.object(_metrics_mod, "summarize", return_value="sentinel output") as mock_fn:
            result = server.get_metrics_summary()
        assert result == "sentinel output"
        mock_fn.assert_called_once()
