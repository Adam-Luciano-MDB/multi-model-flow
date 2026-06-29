"""Tests for mcp/ollama_mcp_server.py

All tests mock httpx and the metrics module so they run fully offline with no
Ollama model required.
"""
import json
import os
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

    def test_uses_first_installed_model_when_model_arg_is_empty(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"response": "ok"}
        with patch("httpx.get", return_value=MagicMock(json=lambda: {
            "models": [{"name": "first:model"}, {"name": "second:model"}]
        })):
            with patch("httpx.post", return_value=mock_resp) as mock_post:
                with patch.object(_metrics_mod, "append"):
                    server.ask_local_model("", "prompt")
        payload = mock_post.call_args.kwargs["json"]
        assert payload["model"] == "first:model"

    def test_env_default_model_wins_over_first_installed(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"response": "ok"}
        with patch.object(server, "DEFAULT_MODEL", "env:model"):
            with patch("httpx.get", return_value=MagicMock(json=lambda: {"models": [{"name": "first:model"}]})):
                with patch("httpx.post", return_value=mock_resp) as mock_post:
                    with patch.object(_metrics_mod, "append"):
                        server.ask_local_model("", "prompt")
        payload = mock_post.call_args.kwargs["json"]
        assert payload["model"] == "env:model"

    def test_returns_error_when_no_model_and_none_installed(self):
        with patch("httpx.get", side_effect=Exception("offline")):
            with patch("httpx.post") as mock_post:
                with patch.object(_metrics_mod, "append"):
                    result = server.ask_local_model("", "prompt")
        assert result.startswith("ERROR")
        mock_post.assert_not_called()


def _chat(message):
    """Build a mock httpx response for an Ollama /api/chat reply."""
    m = MagicMock()
    m.json.return_value = {"message": message}
    m.raise_for_status.return_value = None
    return m


class TestSafeJoin:
    def test_allows_paths_inside_root(self, tmp_path):
        root = str(tmp_path)
        # compare against realpath — _safe_join resolves symlinks (e.g. macOS /var -> /private/var)
        assert server._safe_join(root, "sub/file.py").startswith(os.path.realpath(root))

    def test_rejects_traversal(self, tmp_path):
        with pytest.raises(ValueError):
            server._safe_join(str(tmp_path), "../../etc/passwd")

    def test_rejects_symlink_escape(self, tmp_path):
        # A symlinked directory inside the sandbox must not redirect writes outside it.
        root = tmp_path / "proj"
        root.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (root / "link").symlink_to(outside)  # proj/link -> ../outside
        with pytest.raises(ValueError):
            server._safe_join(str(root), "link/escaped.py")


class TestExecAgentTool:
    def test_read_file(self, tmp_path):
        (tmp_path / "a.txt").write_text("hello")
        assert server._exec_agent_tool("read_file", {"path": "a.txt"}, str(tmp_path)) == "hello"

    def test_write_file_creates_parents(self, tmp_path):
        out = server._exec_agent_tool("write_file", {"path": "sub/b.py", "content": "x=1"}, str(tmp_path))
        assert out.startswith("wrote ")
        assert (tmp_path / "sub" / "b.py").read_text() == "x=1"

    def test_list_files(self, tmp_path):
        (tmp_path / "a.txt").write_text("")
        assert "a.txt" in server._exec_agent_tool("list_files", {"path": "."}, str(tmp_path))

    def test_unknown_tool(self, tmp_path):
        assert server._exec_agent_tool("nope", {}, str(tmp_path)).startswith("ERROR: unknown tool")

    def test_json_string_arguments_parsed(self, tmp_path):
        # Real Ollama returns function.arguments as a JSON string.
        out = server._exec_agent_tool("write_file", '{"path": "c.py", "content": "y=2"}', str(tmp_path))
        assert out.startswith("wrote ")
        assert (tmp_path / "c.py").read_text() == "y=2"

    def test_malformed_json_arguments(self, tmp_path):
        assert server._exec_agent_tool("read_file", "{not json", str(tmp_path)).startswith("ERROR")

    def test_missing_file_is_error_not_crash(self, tmp_path):
        assert server._exec_agent_tool("read_file", {"path": "nope.txt"}, str(tmp_path)).startswith("ERROR")

    def test_non_dict_arguments(self, tmp_path):
        assert server._exec_agent_tool("read_file", 42, str(tmp_path)).startswith("ERROR")


class TestRunOllamaCodingAgent:
    @pytest.fixture(autouse=True)
    def _stub_context_length(self):
        # Stop the agent loop's /api/show context probe from consuming a mocked
        # chat response; context behavior is covered in TestModelContextLength.
        with patch.object(server, "_model_context_length", return_value=200000):
            yield

    def test_writes_file_and_reports_it(self, tmp_path):
        # Round 1: model calls write_file. Round 2: model finishes (no tool calls).
        responses = [
            _chat({"role": "assistant", "tool_calls": [
                {"function": {"name": "write_file",
                              "arguments": {"path": "out.py", "content": "x = 1\n"}}}
            ]}),
            _chat({"role": "assistant", "content": "Done."}),
        ]
        with patch("httpx.get", return_value=MagicMock(json=lambda: {"models": [{"name": "m:1b"}]})):
            with patch("httpx.post", side_effect=responses):
                with patch.object(_metrics_mod, "append"):
                    out = server.run_ollama_coding_agent("write out.py", work_dir=str(tmp_path))
        data = json.loads(out)
        assert data["status"] == "complete"
        assert "out.py" in data["files_written"]
        assert (tmp_path / "out.py").read_text() == "x = 1\n"

    def test_logs_ollama_call_metric_with_tokens(self, tmp_path):
        # Regression: agentic runs must log phase "ollama_call" with char counts
        # so the dashboard's Ollama section (calls + token columns) includes them.
        responses = [
            _chat({"role": "assistant", "tool_calls": [
                {"function": {"name": "write_file",
                              "arguments": {"path": "out.py", "content": "x = 1\n"}}}
            ]}),
            _chat({"role": "assistant", "content": "Done."}),
        ]
        captured = []
        with patch("httpx.get", return_value=MagicMock(json=lambda: {"models": [{"name": "m:1b"}]})):
            with patch("httpx.post", side_effect=responses):
                with patch.object(_metrics_mod, "append", side_effect=lambda r: captured.append(r)):
                    server.run_ollama_coding_agent("write out.py", model="m:1b", work_dir=str(tmp_path))
        rec = captured[-1]
        assert rec["phase"] == "ollama_call"
        assert rec["model"] == "m:1b"
        assert rec["outcome"] == "success"
        assert rec["meta"]["prompt_chars"] > 0
        assert rec["meta"]["response_chars"] > 0
        assert rec["meta"]["mode"] == "ollama-agent"
        assert rec["meta"]["files_written"] == 1

    def test_no_tool_calls_reports_no_files(self, tmp_path):
        with patch("httpx.get", return_value=MagicMock(json=lambda: {"models": [{"name": "m:1b"}]})):
            with patch("httpx.post", side_effect=[_chat({"role": "assistant", "content": "I think..."})]):
                with patch.object(_metrics_mod, "append"):
                    out = server.run_ollama_coding_agent("do it", work_dir=str(tmp_path))
        data = json.loads(out)
        assert data["status"] == "no_tool_calls"
        assert data["files_written"] == []

    def test_max_iterations_cap(self, tmp_path):
        # Model that keeps calling a tool forever must stop at the cap.
        def always_tool():
            return _chat({"role": "assistant", "tool_calls": [
                {"function": {"name": "list_files", "arguments": {"path": "."}}}]})
        with patch("httpx.get", return_value=MagicMock(json=lambda: {"models": [{"name": "m:1b"}]})):
            with patch("httpx.post", side_effect=[always_tool() for _ in range(5)]):
                with patch.object(_metrics_mod, "append"):
                    out = server.run_ollama_coding_agent("loop", model="m:1b",
                                                          work_dir=str(tmp_path), max_iterations=3)
        assert json.loads(out)["status"] == "max_iterations"

    def test_timeout_logs_error_outcome(self, tmp_path):
        captured = []
        with patch("httpx.get", return_value=MagicMock(json=lambda: {"models": [{"name": "m:1b"}]})):
            with patch("httpx.post", side_effect=httpx.TimeoutException("slow")):
                with patch.object(_metrics_mod, "append", side_effect=lambda r: captured.append(r)):
                    out = server.run_ollama_coding_agent("x", model="m:1b", work_dir=str(tmp_path))
        assert out.startswith("ERROR")
        assert captured[-1]["outcome"] == "error"

    def test_json_string_tool_args_through_loop(self, tmp_path):
        responses = [
            _chat({"role": "assistant", "tool_calls": [
                {"function": {"name": "write_file", "arguments": '{"path":"z.py","content":"q=9\\n"}'}}]}),
            _chat({"role": "assistant", "content": "done"}),
        ]
        with patch("httpx.get", return_value=MagicMock(json=lambda: {"models": [{"name": "m:1b"}]})):
            with patch("httpx.post", side_effect=responses):
                with patch.object(_metrics_mod, "append"):
                    out = server.run_ollama_coding_agent("write z", model="m:1b", work_dir=str(tmp_path))
        assert "z.py" in json.loads(out)["files_written"]
        assert (tmp_path / "z.py").read_text() == "q=9\n"

    def test_http_error_signals_tool_support(self, tmp_path):
        err = httpx.HTTPStatusError("bad", request=MagicMock(), response=MagicMock(status_code=400))
        resp = MagicMock()
        resp.raise_for_status.side_effect = err
        with patch("httpx.get", return_value=MagicMock(json=lambda: {"models": [{"name": "m:1b"}]})):
            with patch("httpx.post", return_value=resp):
                with patch.object(_metrics_mod, "append"):
                    out = server.run_ollama_coding_agent("x", work_dir=str(tmp_path))
        assert out.startswith("ERROR")
        assert "tool calling" in out

    def test_error_when_no_models(self, tmp_path):
        with patch("httpx.get", side_effect=Exception("offline")):
            with patch("httpx.post") as mock_post:
                with patch.object(_metrics_mod, "append"):
                    out = server.run_ollama_coding_agent("x", work_dir=str(tmp_path))
        assert out.startswith("ERROR")
        mock_post.assert_not_called()

    def test_traversal_write_is_blocked(self, tmp_path):
        responses = [
            _chat({"role": "assistant", "tool_calls": [
                {"function": {"name": "write_file",
                              "arguments": {"path": "../escape.py", "content": "bad"}}}
            ]}),
            _chat({"role": "assistant", "content": "done"}),
        ]
        with patch("httpx.get", return_value=MagicMock(json=lambda: {"models": [{"name": "m:1b"}]})):
            with patch("httpx.post", side_effect=responses):
                with patch.object(_metrics_mod, "append"):
                    out = server.run_ollama_coding_agent("x", work_dir=str(tmp_path / "proj"))
        data = json.loads(out)
        assert data["files_written"] == []
        assert not (tmp_path / "escape.py").exists()


class TestModelContextLength:
    def _show(self, ctx):
        m = MagicMock(); m.raise_for_status.return_value = None
        m.json.return_value = {"model_info": {"general.architecture": "granite",
                                              "granite.context_length": ctx}}
        return m

    def test_reads_context_length(self):
        with patch("httpx.post", return_value=self._show(131072)):
            assert server._model_context_length("m:1b") == 131072

    def test_unknown_when_missing(self):
        m = MagicMock(); m.raise_for_status.return_value = None
        m.json.return_value = {"model_info": {}}
        with patch("httpx.post", return_value=m):
            assert server._model_context_length("m:1b") is None

    def test_context_warning_triggers_on_overflow(self):
        with patch("httpx.post", return_value=self._show(1000)):
            est, ctx, warn = server._context_warning("m:1b", prompt_chars=8000)  # ~2000 tokens > 1000
        assert ctx == 1000
        assert warn and "exceeds" in warn

    def test_context_warning_silent_when_fits(self):
        with patch("httpx.post", return_value=self._show(100000)):
            est, ctx, warn = server._context_warning("m:1b", prompt_chars=4000)  # ~1000 tokens
        assert warn == ""

    def test_tool_reports_window(self):
        with patch("httpx.get", return_value=MagicMock(json=lambda: {"models": [{"name": "m:1b"}]})):
            with patch("httpx.post", return_value=self._show(8192)):
                out = server.get_model_context_length("m:1b")
        assert "8,192" in out

    def test_tool_not_reported_branch(self):
        m = MagicMock(); m.raise_for_status.return_value = None
        m.json.return_value = {"model_info": {}}
        with patch("httpx.get", return_value=MagicMock(json=lambda: {"models": [{"name": "m:1b"}]})):
            with patch("httpx.post", return_value=m):
                out = server.get_model_context_length("m:1b")
        assert "not reported" in out

    def test_tool_no_models(self):
        with patch("httpx.get", side_effect=Exception("offline")):
            out = server.get_model_context_length("")
        assert out.startswith("ERROR")


class TestTokenToolWrappers:
    def test_get_real_token_usage_delegates(self):
        with patch.object(server._token_usage, "summarize_real_usage", return_value="SENTINEL") as fn:
            assert server.get_real_token_usage() == "SENTINEL"
        fn.assert_called_once()

    def test_check_token_budget_forwards_limit(self):
        with patch.object(server._token_usage, "summarize_token_budget", return_value="OK") as fn:
            assert server.check_token_budget(50000) == "OK"
        fn.assert_called_once_with(50000)


class TestEstimateContextFit:
    def _show(self, ctx):
        m = MagicMock(); m.raise_for_status.return_value = None
        m.json.return_value = {"model_info": {"x.context_length": ctx}}
        return m

    def test_fits_when_small(self, tmp_path):
        f = tmp_path / "a.py"; f.write_text("x" * 400)  # ~100 tokens
        with patch("httpx.get", return_value=MagicMock(json=lambda: {"models": [{"name": "m:1b"}]})):
            with patch("httpx.post", return_value=self._show(100000)):
                out = json.loads(server.estimate_context_fit(json.dumps([str(f)]), "m:1b"))
        assert out["fits"] is True
        assert out["est_tokens"] == 100

    def test_overflow_when_large(self, tmp_path):
        f = tmp_path / "big.py"; f.write_text("x" * 40000)  # ~10000 tokens
        with patch("httpx.get", return_value=MagicMock(json=lambda: {"models": [{"name": "m:1b"}]})):
            with patch("httpx.post", return_value=self._show(2048)):
                out = json.loads(server.estimate_context_fit(json.dumps([str(f)]), "m:1b"))
        assert out["fits"] is False
        assert out["context_window"] == 2048

    def test_extra_chars_counted(self, tmp_path):
        with patch("httpx.get", return_value=MagicMock(json=lambda: {"models": [{"name": "m:1b"}]})):
            with patch("httpx.post", return_value=self._show(10)):
                out = json.loads(server.estimate_context_fit(json.dumps([]), "m:1b", extra_chars=400))
        assert out["est_tokens"] == 100  # 400 // 4
        assert out["fits"] is False  # 100 > 10

    def test_missing_files_reported_not_fatal(self, tmp_path):
        with patch("httpx.get", return_value=MagicMock(json=lambda: {"models": [{"name": "m:1b"}]})):
            with patch("httpx.post", return_value=self._show(100000)):
                out = json.loads(server.estimate_context_fit(json.dumps(["/no/such/file.py"]), "m:1b"))
        assert out["missing"] == ["/no/such/file.py"]
        assert out["fits"] is True

    def test_unknown_window_defaults_to_fits(self, tmp_path):
        f = tmp_path / "a.py"; f.write_text("x" * 40000)
        m = MagicMock(); m.raise_for_status.return_value = None
        m.json.return_value = {"model_info": {}}  # no context_length
        with patch("httpx.get", return_value=MagicMock(json=lambda: {"models": [{"name": "m:1b"}]})):
            with patch("httpx.post", return_value=m):
                out = json.loads(server.estimate_context_fit(json.dumps([str(f)]), "m:1b"))
        assert out["context_window"] is None
        assert out["fits"] is True  # can't prove overflow

    def test_bad_json_errors(self):
        with patch("httpx.get", return_value=MagicMock(json=lambda: {"models": [{"name": "m:1b"}]})):
            out = server.estimate_context_fit("not-json", "m:1b")
        assert out.startswith("ERROR")


class TestListModelsForSelection:
    def test_numbered_list_marks_first_as_default(self):
        with patch("httpx.get", return_value=MagicMock(json=lambda: {
            "models": [{"name": "alpha:7b"}, {"name": "beta:3b"}]
        })):
            out = server.list_models_for_selection()
        assert "1. alpha:7b" in out
        assert "default" in out.split("\n")[1]  # first entry marked default
        assert "2. beta:3b" in out

    def test_message_when_no_models(self):
        with patch("httpx.get", return_value=MagicMock(json=lambda: {"models": []})):
            out = server.list_models_for_selection()
        assert "No local models" in out

    def test_surfaces_offline_error(self):
        with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
            out = server.list_models_for_selection()
        assert "ERROR" in out


class TestAskLocalModelForCode:
    def test_returns_string_on_success(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"response": "def hello(): pass"}
        with patch("httpx.get", return_value=MagicMock(json=lambda: {"models": [{"name": "qwen2.5-coder:7b"}]})):
            with patch("httpx.post", return_value=mock_resp):
                with patch.object(_metrics_mod, "append"):
                    result = server.ask_local_model_for_code("write a hello function", language="Python")
        assert result == "def hello(): pass"

    def test_uses_first_installed_model_no_hardcoded_preference(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"response": "ok"}
        # devstral present but NOT first — must pick the first installed, not devstral
        with patch("httpx.get", return_value=MagicMock(json=lambda: {
            "models": [{"name": "alpha:7b"}, {"name": "devstral:latest"}]
        })):
            with patch("httpx.post", return_value=mock_resp) as mock_post:
                with patch.object(_metrics_mod, "append"):
                    server.ask_local_model_for_code("prompt")
        payload = mock_post.call_args.kwargs["json"]
        assert payload["model"] == "alpha:7b"

    def test_returns_error_when_no_models_available(self):
        with patch("httpx.get", side_effect=Exception("offline")):
            with patch("httpx.post") as mock_post:
                with patch.object(_metrics_mod, "append"):
                    result = server.ask_local_model_for_code("prompt")
        assert result.startswith("ERROR")
        mock_post.assert_not_called()

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


class TestOpenMetricsDashboard:
    def test_reuses_existing_server_without_spawning(self):
        with patch.object(server, "_port_is_open", return_value=True), \
             patch("subprocess.Popen") as popen:
            result = server.open_metrics_dashboard(8765)
        assert "already running" in result
        assert "http://127.0.0.1:8765" in result
        popen.assert_not_called()

    def test_spawns_detached_dashboard_when_port_free(self):
        # First check: free -> spawn. Readiness loop: open -> return.
        with patch.object(server, "_port_is_open", side_effect=[False, True]), \
             patch("subprocess.Popen") as popen:
            result = server.open_metrics_dashboard(8765)
        assert "started at http://127.0.0.1:8765" in result
        popen.assert_called_once()
        # Detached so the dashboard outlives the MCP server.
        assert popen.call_args.kwargs.get("start_new_session") is True

    def test_returns_error_when_ui_script_missing(self):
        with patch.object(server, "_port_is_open", return_value=False), \
             patch("os.path.exists", return_value=False), \
             patch("subprocess.Popen") as popen:
            result = server.open_metrics_dashboard(8765)
        assert result.startswith("ERROR")
        popen.assert_not_called()

    def test_reports_error_when_spawn_fails(self):
        with patch.object(server, "_port_is_open", return_value=False), \
             patch("os.path.exists", return_value=True), \
             patch("subprocess.Popen", side_effect=OSError("boom")):
            result = server.open_metrics_dashboard(8765)
        assert result.startswith("ERROR")


def _orouter(content):
    m = MagicMock(); m.raise_for_status.return_value = None
    m.json.return_value = {"choices": [{"message": {"content": content}}]}
    return m


class TestAskOpenRouterForCode:
    def test_success(self):
        with patch.object(server, "OPENROUTER_API_KEY", "sk-or-key"), \
             patch.object(server, "OPENROUTER_MODEL", "qwen/q"), \
             patch("httpx.post", return_value=_orouter("def f(): pass")), \
             patch.object(_metrics_mod, "append"):
            out = server.ask_openrouter_for_code("write f", language="Python")
        assert out == "def f(): pass"

    def test_missing_api_key(self):
        with patch.object(server, "OPENROUTER_API_KEY", ""):
            out = server.ask_openrouter_for_code("x")
        assert out.startswith("ERROR") and "OPENROUTER_API_KEY" in out

    def test_missing_model(self):
        with patch.object(server, "OPENROUTER_API_KEY", "sk"), \
             patch.object(server, "OPENROUTER_MODEL", ""):
            out = server.ask_openrouter_for_code("x")
        assert out.startswith("ERROR") and "model" in out.lower()

    def test_http_error(self):
        err = httpx.HTTPStatusError("bad", request=MagicMock(), response=MagicMock(status_code=401))
        resp = MagicMock(); resp.raise_for_status.side_effect = err
        with patch.object(server, "OPENROUTER_API_KEY", "sk"), \
             patch.object(server, "OPENROUTER_MODEL", "qwen/q"), \
             patch("httpx.post", return_value=resp), \
             patch.object(_metrics_mod, "append"):
            out = server.ask_openrouter_for_code("x")
        assert out.startswith("ERROR") and "401" in out

    def test_context_and_auth_in_request(self):
        captured = {}
        def fake_post(url, headers=None, json=None, timeout=None):
            captured["url"] = url; captured["headers"] = headers; captured["json"] = json
            return _orouter("ok")
        with patch.object(server, "OPENROUTER_API_KEY", "sk-xyz"), \
             patch.object(server, "OPENROUTER_MODEL", "qwen/q"), \
             patch("httpx.post", side_effect=fake_post), \
             patch.object(_metrics_mod, "append"):
            server.ask_openrouter_for_code("add a method", context="class Foo: pass")
        assert captured["headers"]["Authorization"] == "Bearer sk-xyz"
        assert "/chat/completions" in captured["url"]
        user_msg = captured["json"]["messages"][-1]["content"]
        assert "class Foo: pass" in user_msg and "add a method" in user_msg

    def test_explicit_model_overrides_env(self):
        captured = {}
        def fake_post(url, headers=None, json=None, timeout=None):
            captured["json"] = json; return _orouter("ok")
        with patch.object(server, "OPENROUTER_API_KEY", "sk"), \
             patch.object(server, "OPENROUTER_MODEL", "env/model"), \
             patch("httpx.post", side_effect=fake_post), \
             patch.object(_metrics_mod, "append"):
            server.ask_openrouter_for_code("x", model="explicit/model")
        assert captured["json"]["model"] == "explicit/model"

    def test_logs_metric_with_openrouter_provider(self):
        captured = []
        with patch.object(server, "OPENROUTER_API_KEY", "sk"), \
             patch.object(server, "OPENROUTER_MODEL", "qwen/q"), \
             patch("httpx.post", return_value=_orouter("code")), \
             patch.object(_metrics_mod, "append", side_effect=lambda r: captured.append(r)):
            server.ask_openrouter_for_code("x")
        rec = captured[-1]
        assert rec["phase"] == "ollama_call"
        assert rec["meta"]["provider"] == "openrouter"
        assert rec["outcome"] == "success"
