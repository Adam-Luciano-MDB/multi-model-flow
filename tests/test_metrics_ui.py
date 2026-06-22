"""Tests for mcp/metrics_ui.py"""
import io
import json
import threading
from http.client import HTTPConnection
from http.server import HTTPServer
from unittest.mock import patch

import metrics
import metrics_ui


class TestRenderMetricsJson:
    def test_returns_valid_json(self, tmp_path):
        f = str(tmp_path / "m.jsonl")
        with open(f, "w") as fh:
            fh.write(json.dumps({
                "ts": 1750000000.0,
                "phase": "workflow",
                "model": "m",
                "outcome": "approved",
                "meta": {"retries": 0, "task": "test task"},
            }) + "\n")
        with patch.object(metrics, "METRICS_FILE", f):
            json_str = metrics_ui.render_metrics_json()
        # Should be valid JSON
        data = json.loads(json_str)
        assert isinstance(data, dict)
        assert "workflow" in data
        assert "ollama" in data

    def test_render_survives_transcript_parse_error(self):
        # render_metrics_json must never crash if transcript parsing raises.
        import token_usage
        with patch.object(token_usage, "aggregate_real_usage", side_effect=RuntimeError("boom")):
            out = metrics_ui.render_metrics_json()
        data = json.loads(out)
        assert data["real_tokens"]["by_tier"] == []
        assert data["real_tokens"]["total_tokens"] == 0

    def test_json_contains_claude_key(self, tmp_path):
        f = str(tmp_path / "m.jsonl")
        with open(f, "w") as fh:
            fh.write(json.dumps({
                "ts": 1750000000.0, "phase": "workflow", "outcome": "approved",
                "meta": {"retries": 0, "claude_calls": {"opus": 1, "haiku": 5, "sonnet": 1, "fable": 0}},
            }) + "\n")
        with patch.object(metrics, "METRICS_FILE", f):
            json_str = metrics_ui.render_metrics_json()
        data = json.loads(json_str)
        assert "claude" in data
        assert data["claude"]["total_calls"] == 7
        assert len(data["claude"]["by_tier"]) == 3  # opus, haiku, sonnet (fable=0 omitted)
        assert data["claude"]["est_total_cost_usd"] > 0

    def test_json_contains_workflow_and_ollama_keys(self, tmp_path):
        f = str(tmp_path / "m.jsonl")
        with open(f, "w") as fh:
            fh.write(json.dumps({
                "ts": 1750000000.0,
                "phase": "workflow",
                "outcome": "approved",
                "meta": {"retries": 0},
            }) + "\n")
            fh.write(json.dumps({
                "ts": 1750000001.0,
                "phase": "ollama_call",
                "model": "test-model",
                "outcome": "success",
                "meta": {"prompt_chars": 1000, "response_chars": 100, "duration_ms": 2000},
            }) + "\n")
        with patch.object(metrics, "METRICS_FILE", f):
            json_str = metrics_ui.render_metrics_json()
        data = json.loads(json_str)
        assert "workflow" in data
        assert "ollama" in data
        assert data["workflow"]["total"] == 1
        assert data["ollama"]["total"] == 1

    def test_json_round_trips_with_sample_data(self, tmp_path):
        f = str(tmp_path / "m.jsonl")
        with open(f, "w") as fh:
            for i in range(3):
                fh.write(json.dumps({
                    "ts": 1750000000.0 + i,
                    "phase": "workflow",
                    "outcome": "approved",
                    "meta": {"retries": i, "task": f"task-{i}"},
                }) + "\n")
        with patch.object(metrics, "METRICS_FILE", f):
            json_str = metrics_ui.render_metrics_json()
        data = json.loads(json_str)
        assert data["workflow"]["total"] == 3
        assert data["workflow"]["avg_retries"] == (0 + 1 + 2) / 3

    def test_ollama_section_round_trips(self, tmp_path):
        f = str(tmp_path / "m.jsonl")
        recs = [
            {"ts": 1.0, "phase": "ollama_call", "model": "qwen", "outcome": "success",
             "meta": {"prompt_chars": 400, "response_chars": 200, "duration_ms": 1000}},
            {"ts": 2.0, "phase": "ollama_call", "model": "qwen", "outcome": "error",
             "meta": {"prompt_chars": 400, "response_chars": 0, "duration_ms": 3000}},
            {"ts": 3.0, "phase": "ollama_call", "model": "llama", "outcome": "success",
             "meta": {"prompt_chars": 800, "response_chars": 200, "duration_ms": 2000}},
        ]
        with open(f, "w") as fh:
            for r in recs:
                fh.write(json.dumps(r) + "\n")
        with patch.object(metrics, "METRICS_FILE", f):
            data = json.loads(metrics_ui.render_metrics_json())
        o = data["ollama"]
        assert o["total"] == 3
        # by_model sorted alphabetically: llama, qwen
        assert [m["model"] for m in o["by_model"]] == ["llama", "qwen"]
        qwen = next(m for m in o["by_model"] if m["model"] == "qwen")
        assert qwen["calls"] == 2
        assert qwen["errors"] == 1
        assert qwen["avg_latency_ms"] == 2000.0  # (1000 + 3000) / 2
        assert o["approx_tokens_in"] == (400 + 400 + 800) // 4
        assert o["approx_tokens_out"] == (200 + 0 + 200) // 4


class TestIndexHtml:
    def test_index_html_is_nonempty_string(self):
        assert isinstance(metrics_ui.INDEX_HTML, str)
        assert len(metrics_ui.INDEX_HTML) > 0

    def test_index_html_contains_fetch(self):
        assert "fetch" in metrics_ui.INDEX_HTML

    def test_index_html_contains_chart(self):
        assert "chart" in metrics_ui.INDEX_HTML.lower()

    def test_index_html_contains_cdn_script(self):
        assert "cdn.jsdelivr.net" in metrics_ui.INDEX_HTML

    def test_index_html_is_valid_html(self):
        # Check basic HTML structure
        assert "<!DOCTYPE html>" in metrics_ui.INDEX_HTML
        assert "</html>" in metrics_ui.INDEX_HTML
        assert "<title>" in metrics_ui.INDEX_HTML

    def test_escapehtml_helper_is_defined(self):
        html = metrics_ui.INDEX_HTML
        assert "function escapeHtml(s)" in html
        # all five HTML-significant characters must be escaped
        assert "/&/g, '&amp;'" in html
        assert "/</g, '&lt;'" in html
        assert "/>/g, '&gt;'" in html
        assert "/'/g, '&#39;'" in html
        assert "&quot;" in html

    def test_claude_section_present_in_html(self):
        html = metrics_ui.INDEX_HTML
        assert "Claude API Usage" in html
        assert "Ollama Savings" in html
        assert "Claude Calls" in html
        assert "est_total_cost_usd" in html
        assert "est_ollama_savings_usd" in html

    def test_cost_comparison_fields_in_html(self):
        html = metrics_ui.INDEX_HTML
        assert "est_all_opus_cost_usd" in html
        assert "est_all_sonnet_cost_usd" in html
        assert "savings_vs_opus_usd" in html
        assert "savings_vs_sonnet_usd" in html
        assert "If all-Opus" in html
        assert "If all-Sonnet" in html
        assert "renderCostChart" in html

    def test_dashboard_title_is_mariadb(self):
        html = metrics_ui.INDEX_HTML
        assert "MariaDB Multi-Model-Flow Metrics Dashboard" in html

    def test_test_gate_failed_has_outcome_color(self):
        # The hard-test-gate outcome must render as a failure color, not the default.
        html = metrics_ui.INDEX_HTML
        assert "'test_gate_failed':" in html

    def test_user_controlled_fields_are_html_escaped(self):
        # Regression guard: stored XSS via task/outcome/model name. If a future
        # edit drops escapeHtml() around any of these interpolations, this fails.
        html = metrics_ui.INDEX_HTML
        assert "escapeHtml(run.outcome)" in html
        assert "escapeHtml(run.task.substring(0, 80))" in html
        assert "escapeHtml(model.model)" in html
        # raw, un-escaped interpolations must NOT be present
        assert "${run.outcome}" not in html
        assert "${model.model}" not in html


class TestMetricsRequestHandler:
    """Integration tests for MetricsRequestHandler via a real HTTPServer."""

    def _start_server(self, tmp_metrics_file):
        """Spin up a server on a random port; return (server, port, stop_event)."""
        httpd = HTTPServer(("127.0.0.1", 0), metrics_ui.MetricsRequestHandler)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        return httpd, port

    def test_get_root_returns_200_html(self, tmp_path):
        f = str(tmp_path / "m.jsonl")
        with patch.object(metrics, "METRICS_FILE", f):
            httpd, port = self._start_server(f)
            try:
                conn = HTTPConnection("127.0.0.1", port)
                conn.request("GET", "/")
                resp = conn.getresponse()
                assert resp.status == 200
                assert "text/html" in resp.getheader("Content-Type", "")
                body = resp.read().decode("utf-8")
                assert "<!DOCTYPE html>" in body
            finally:
                httpd.shutdown()

    def test_get_api_metrics_returns_200_json(self, tmp_path):
        f = str(tmp_path / "m.jsonl")
        with open(f, "w") as fh:
            fh.write(json.dumps({
                "ts": 1750000000.0,
                "phase": "workflow",
                "outcome": "approved",
                "meta": {"retries": 0, "task": "test"},
            }) + "\n")
        with patch.object(metrics, "METRICS_FILE", f):
            httpd, port = self._start_server(f)
            try:
                conn = HTTPConnection("127.0.0.1", port)
                conn.request("GET", "/api/metrics")
                resp = conn.getresponse()
                assert resp.status == 200
                assert resp.getheader("Content-Type") == "application/json"
                data = json.loads(resp.read().decode("utf-8"))
                assert data["workflow"]["total"] == 1
                assert data["workflow"]["outcome_counts"] == {"approved": 1}
            finally:
                httpd.shutdown()

    def test_get_unknown_path_returns_404(self, tmp_path):
        f = str(tmp_path / "m.jsonl")
        with patch.object(metrics, "METRICS_FILE", f):
            httpd, port = self._start_server(f)
            try:
                conn = HTTPConnection("127.0.0.1", port)
                conn.request("GET", "/does-not-exist")
                resp = conn.getresponse()
                assert resp.status == 404
            finally:
                httpd.shutdown()

    def test_get_api_metrics_empty_file_returns_zero_totals(self, tmp_path):
        f = str(tmp_path / "m.jsonl")
        with patch.object(metrics, "METRICS_FILE", f):
            httpd, port = self._start_server(f)
            try:
                conn = HTTPConnection("127.0.0.1", port)
                conn.request("GET", "/api/metrics")
                resp = conn.getresponse()
                assert resp.status == 200
                data = json.loads(resp.read().decode("utf-8"))
                assert data["workflow"]["total"] == 0
                assert data["ollama"]["total"] == 0
            finally:
                httpd.shutdown()

    def test_get_api_metrics_with_ollama_data(self, tmp_path):
        f = str(tmp_path / "m.jsonl")
        with open(f, "w") as fh:
            for i in range(2):
                fh.write(json.dumps({
                    "ts": 1.0 + i, "phase": "ollama_call", "model": "qwen",
                    "outcome": "success",
                    "meta": {"prompt_chars": 400, "response_chars": 200, "duration_ms": 1000},
                }) + "\n")
            fh.write(json.dumps({
                "ts": 3.0, "phase": "ollama_call", "model": "llama", "outcome": "success",
                "meta": {"prompt_chars": 800, "response_chars": 200, "duration_ms": 2000},
            }) + "\n")
        with patch.object(metrics, "METRICS_FILE", f):
            httpd, port = self._start_server(f)
            try:
                conn = HTTPConnection("127.0.0.1", port)
                conn.request("GET", "/api/metrics")
                resp = conn.getresponse()
                assert resp.status == 200
                data = json.loads(resp.read().decode("utf-8"))
                assert data["ollama"]["total"] == 3
                assert len(data["ollama"]["by_model"]) == 2
                assert data["ollama"]["approx_tokens_in"] == (400 + 400 + 800) // 4
            finally:
                httpd.shutdown()


class TestMain:
    """main() binds the real server; patch HTTPServer so it neither binds nor blocks."""

    def test_uses_default_host_port(self):
        with patch("metrics_ui.HTTPServer") as mock_server:
            mock_server.return_value.serve_forever.side_effect = KeyboardInterrupt
            with patch("sys.argv", ["metrics_ui.py"]):
                metrics_ui.main()
        assert mock_server.call_args[0][0] == ("127.0.0.1", 8765)

    def test_respects_host_port_flags(self):
        with patch("metrics_ui.HTTPServer") as mock_server:
            mock_server.return_value.serve_forever.side_effect = KeyboardInterrupt
            with patch("sys.argv", ["metrics_ui.py", "--host", "0.0.0.0", "--port", "9999"]):
                metrics_ui.main()
        assert mock_server.call_args[0][0] == ("0.0.0.0", 9999)
