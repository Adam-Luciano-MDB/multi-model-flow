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
