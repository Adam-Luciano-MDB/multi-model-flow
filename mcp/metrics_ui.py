"""
Metrics UI — a lightweight read-only web dashboard for multi-model-flow metrics.

Serves a self-contained HTML page visualizing workflow outcomes, Ollama call
statistics, and recent runs. Uses stdlib-only HTTP server with CDN-loaded Chart.js
for charts. Data fetched from /api/metrics endpoint which calls metrics.aggregate().
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

# Allow importing the sibling metrics module regardless of working directory.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import metrics


INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MariaDB Multi-Model-Flow Metrics Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.js"></script>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: #333;
            padding: 20px;
            min-height: 100vh;
        }
        .container {
            max-width: 1400px;
            margin: 0 auto;
        }
        h1 {
            color: white;
            margin-bottom: 30px;
            text-align: center;
        }
        .cards {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        .card {
            background: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
        }
        .card h3 {
            font-size: 14px;
            text-transform: uppercase;
            color: #666;
            margin-bottom: 10px;
        }
        .card .value {
            font-size: 28px;
            font-weight: bold;
            color: #667eea;
        }
        .card .subtext {
            font-size: 12px;
            color: #999;
            margin-top: 8px;
        }
        .charts {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(400px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        .chart-card {
            background: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
        }
        .chart-card h2 {
            font-size: 16px;
            margin-bottom: 15px;
            color: #333;
        }
        .chart-container {
            position: relative;
            height: 300px;
        }
        .table-card {
            background: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
            margin-bottom: 30px;
            overflow-x: auto;
        }
        .table-card h2 {
            font-size: 16px;
            margin-bottom: 15px;
            color: #333;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 14px;
        }
        th {
            background: #f5f5f5;
            padding: 12px;
            text-align: left;
            font-weight: 600;
            color: #666;
            border-bottom: 2px solid #e0e0e0;
        }
        td {
            padding: 12px;
            border-bottom: 1px solid #e0e0e0;
        }
        tr:hover {
            background: #fafafa;
        }
        .status-approved {
            color: #28a745;
            font-weight: 600;
        }
        .status-rejected {
            color: #dc3545;
            font-weight: 600;
        }
        .status-error {
            color: #dc3545;
            font-weight: 600;
        }
        .savings {
            color: #28a745;
            font-weight: 600;
        }
        .loading {
            text-align: center;
            color: white;
            font-size: 18px;
            padding: 40px;
        }
        .error {
            background: #f8d7da;
            color: #721c24;
            padding: 15px;
            border-radius: 8px;
            margin-bottom: 20px;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>MariaDB Multi-Model-Flow Metrics Dashboard</h1>
        <div id="content">
            <div class="loading">Loading metrics...</div>
        </div>
    </div>

    <script>
        function escapeHtml(s) {
            return String(s)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#39;');
        }

        async function loadMetrics() {
            try {
                const response = await fetch('/api/metrics');
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}`);
                }
                const data = await response.json();
                renderDashboard(data);
            } catch (error) {
                document.getElementById('content').innerHTML =
                    `<div class="error">Failed to load metrics: ${error.message}</div>`;
            }
        }

        function renderDashboard(data) {
            const workflow = data.workflow;
            const ollama = data.ollama;
            const claude = data.claude || { total_calls: 0, by_tier: [], est_total_cost_usd: 0, est_ollama_savings_usd: 0 };
            let html = '';

            // Summary cards
            html += '<div class="cards">';
            html += `
                <div class="card">
                    <h3>Total Runs</h3>
                    <div class="value">${workflow.total}</div>
                </div>
                <div class="card">
                    <h3>Avg Retries</h3>
                    <div class="value">${workflow.avg_retries.toFixed(1)}</div>
                </div>
                <div class="card">
                    <h3>Ollama Calls</h3>
                    <div class="value">${ollama.total}</div>
                </div>
                <div class="card">
                    <h3>Approx Tokens In</h3>
                    <div class="value">${ollama.approx_tokens_in.toLocaleString()}</div>
                </div>
                <div class="card">
                    <h3>Approx Tokens Out</h3>
                    <div class="value">${ollama.approx_tokens_out.toLocaleString()}</div>
                </div>
                <div class="card">
                    <h3>Claude Calls</h3>
                    <div class="value">${claude.total_calls}</div>
                    <div class="subtext">~$${claude.est_total_cost_usd.toFixed(2)} est.</div>
                </div>
                <div class="card">
                    <h3>Ollama Savings</h3>
                    <div class="value savings">~$${claude.est_ollama_savings_usd.toFixed(2)}</div>
                    <div class="subtext">vs Haiku for Ollama steps</div>
                </div>
                <div class="card">
                    <h3>Savings vs All-Opus</h3>
                    <div class="value savings">~$${(claude.savings_vs_opus_usd || 0).toFixed(2)}</div>
                    <div class="subtext">actual vs ${(claude.est_all_opus_cost_usd || 0).toFixed(2)} all-Opus</div>
                </div>
                <div class="card">
                    <h3>Savings vs All-Sonnet</h3>
                    <div class="value savings">~$${(claude.savings_vs_sonnet_usd || 0).toFixed(2)}</div>
                    <div class="subtext">actual vs ${(claude.est_all_sonnet_cost_usd || 0).toFixed(2)} all-Sonnet</div>
                </div>
            `;
            html += '</div>';

            // Charts
            html += '<div class="charts">';

            // Outcomes pie chart
            if (workflow.total > 0) {
                html += `
                    <div class="chart-card">
                        <h2>Workflow Outcomes</h2>
                        <div class="chart-container">
                            <canvas id="outcomesChart"></canvas>
                        </div>
                    </div>
                `;
            }

            // Per-model calls bar chart
            if (ollama.total > 0 && ollama.by_model.length > 0) {
                html += `
                    <div class="chart-card">
                        <h2>Calls by Model</h2>
                        <div class="chart-container">
                            <canvas id="callsChart"></canvas>
                        </div>
                    </div>
                `;
            }

            // Cost comparison chart
            if (claude.total_calls > 0) {
                html += `
                    <div class="chart-card">
                        <h2>Cost Comparison</h2>
                        <div class="chart-container">
                            <canvas id="costChart"></canvas>
                        </div>
                    </div>
                `;
            }

            html += '</div>';

            // Recent runs table
            if (workflow.recent.length > 0) {
                html += `
                    <div class="table-card">
                        <h2>Recent Runs</h2>
                        <table>
                            <thead>
                                <tr>
                                    <th>Timestamp</th>
                                    <th>Outcome</th>
                                    <th>Steps</th>
                                    <th>Files</th>
                                    <th>Retries</th>
                                    <th>Task</th>
                                </tr>
                            </thead>
                            <tbody>
                `;
                for (const run of workflow.recent) {
                    const ts = new Date(run.ts * 1000).toLocaleString();
                    const statusClass = `status-${escapeHtml(run.outcome)}`;
                    const task = escapeHtml(run.task.substring(0, 80));
                    html += `
                        <tr>
                            <td>${ts}</td>
                            <td class="${statusClass}">${escapeHtml(run.outcome)}</td>
                            <td>${run.steps_planned !== null ? run.steps_planned : '—'}</td>
                            <td>${run.files_written !== null ? run.files_written : '—'}</td>
                            <td>${run.retries}</td>
                            <td>${task}</td>
                        </tr>
                    `;
                }
                html += `
                            </tbody>
                        </table>
                    </div>
                `;
            }

            // Per-model latency table
            if (ollama.by_model.length > 0) {
                html += `
                    <div class="table-card">
                        <h2>Ollama Models</h2>
                        <table>
                            <thead>
                                <tr>
                                    <th>Model</th>
                                    <th>Calls</th>
                                    <th>Avg Latency</th>
                                    <th>Errors</th>
                                </tr>
                            </thead>
                            <tbody>
                `;
                for (const model of ollama.by_model) {
                    const latency = model.avg_latency_ms !== null
                        ? (model.avg_latency_ms / 1000).toFixed(1) + 's'
                        : 'n/a';
                    html += `
                        <tr>
                            <td>${escapeHtml(model.model)}</td>
                            <td>${model.calls}</td>
                            <td>${latency}</td>
                            <td>${model.errors}</td>
                        </tr>
                    `;
                }
                html += `
                            </tbody>
                        </table>
                    </div>
                `;
            }

            // Claude API usage breakdown
            if (claude.by_tier.length > 0) {
                html += `
                    <div class="table-card">
                        <h2>Claude API Usage (estimated)</h2>
                        <table>
                            <thead>
                                <tr>
                                    <th>Tier</th>
                                    <th>Calls</th>
                                    <th>Est. Tokens</th>
                                    <th>Est. Cost</th>
                                </tr>
                            </thead>
                            <tbody>
                `;
                const totalEstTokens = claude.by_tier.reduce((s, t) => s + (t.est_tokens || 0), 0);
                for (const t of claude.by_tier) {
                    html += `
                        <tr>
                            <td>${escapeHtml(t.tier)}</td>
                            <td>${t.calls}</td>
                            <td>${(t.est_tokens || 0).toLocaleString()}</td>
                            <td>~$${t.est_cost_usd.toFixed(3)}</td>
                        </tr>
                    `;
                }
                html += `
                        <tr style="font-weight:600;border-top:2px solid #e0e0e0;">
                            <td>Actual (mixed tiers)</td>
                            <td>${claude.total_calls}</td>
                            <td>${totalEstTokens.toLocaleString()}</td>
                            <td>~$${claude.est_total_cost_usd.toFixed(3)}</td>
                        </tr>
                        <tr style="color:#dc3545;">
                            <td>If all-Opus</td>
                            <td>${claude.total_calls}</td>
                            <td>—</td>
                            <td>~$${(claude.est_all_opus_cost_usd || 0).toFixed(3)}</td>
                        </tr>
                        <tr style="color:#fd7e14;">
                            <td>If all-Sonnet</td>
                            <td>${claude.total_calls}</td>
                            <td>—</td>
                            <td>~$${(claude.est_all_sonnet_cost_usd || 0).toFixed(3)}</td>
                        </tr>
                `;
                if (claude.savings_vs_opus_usd > 0) {
                    html += `
                        <tr style="color:#28a745;font-weight:600;border-top:2px solid #e0e0e0;">
                            <td colspan="3">Saved vs all-Opus</td>
                            <td>~$${(claude.savings_vs_opus_usd || 0).toFixed(3)}</td>
                        </tr>
                        <tr style="color:#28a745;">
                            <td colspan="3">Saved vs all-Sonnet</td>
                            <td>~$${(claude.savings_vs_sonnet_usd || 0).toFixed(3)}</td>
                        </tr>
                    `;
                }
                if (claude.est_ollama_savings_usd > 0) {
                    html += `
                        <tr style="color:#28a745;">
                            <td colspan="3">Ollama offloaded (saved vs Haiku)</td>
                            <td>~$${claude.est_ollama_savings_usd.toFixed(3)}</td>
                        </tr>
                    `;
                }
                html += `
                            </tbody>
                        </table>
                        <p style="font-size:11px;color:#999;margin-top:8px;">
                            Estimated from agent call counts × typical prompt sizes.
                            See Claude Console for exact billing.
                        </p>
                    </div>
                `;
            }

            document.getElementById('content').innerHTML = html;

            // Render charts
            if (workflow.total > 0) {
                renderOutcomesChart(workflow.outcome_counts);
            }
            if (ollama.total > 0 && ollama.by_model.length > 0) {
                renderCallsChart(ollama.by_model);
            }
            if (claude.total_calls > 0) {
                renderCostChart(claude);
            }
        }

        function renderOutcomesChart(outcomeCounts) {
            const ctx = document.getElementById('outcomesChart').getContext('2d');
            const labels = Object.keys(outcomeCounts);
            const data = Object.values(outcomeCounts);
            const colors = {
                'approved': '#28a745',
                'rejected': '#dc3545',
                'error': '#fd7e14',
                'execution_failed': '#6c757d',
            };
            const backgroundColors = labels.map(label => colors[label] || '#667eea');

            new Chart(ctx, {
                type: 'doughnut',
                data: {
                    labels: labels,
                    datasets: [{
                        data: data,
                        backgroundColor: backgroundColors,
                        borderColor: 'white',
                        borderWidth: 2,
                    }],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: {
                            position: 'bottom',
                        },
                    },
                },
            });
        }

        function renderCallsChart(byModel) {
            const ctx = document.getElementById('callsChart').getContext('2d');
            const labels = byModel.map(m => m.model);
            const calls = byModel.map(m => m.calls);

            new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: labels,
                    datasets: [{
                        label: 'Calls',
                        data: calls,
                        backgroundColor: '#667eea',
                        borderColor: '#667eea',
                        borderWidth: 1,
                    }],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    indexAxis: 'y',
                    plugins: {
                        legend: {
                            display: false,
                        },
                    },
                    scales: {
                        x: {
                            beginAtZero: true,
                            ticks: {
                                stepSize: 1,
                            },
                        },
                    },
                },
            });
        }

        function renderCostChart(claude) {
            const ctx = document.getElementById('costChart').getContext('2d');
            new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: ['Actual (mixed)', 'If all-Sonnet', 'If all-Opus'],
                    datasets: [{
                        label: 'Estimated Cost (USD)',
                        data: [
                            claude.est_total_cost_usd,
                            claude.est_all_sonnet_cost_usd || 0,
                            claude.est_all_opus_cost_usd || 0,
                        ],
                        backgroundColor: ['#28a745', '#fd7e14', '#dc3545'],
                        borderColor: ['#28a745', '#fd7e14', '#dc3545'],
                        borderWidth: 1,
                    }],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { display: false },
                        tooltip: {
                            callbacks: {
                                label: (ctx) => ` ~$${ctx.parsed.y.toFixed(3)}`,
                            },
                        },
                    },
                    scales: {
                        y: {
                            beginAtZero: true,
                            ticks: {
                                callback: (v) => '$' + v.toFixed(2),
                            },
                        },
                    },
                },
            });
        }

        // Load metrics on page load
        loadMetrics();
    </script>
</body>
</html>"""


def render_metrics_json() -> str:
    """Return JSON string of aggregated metrics."""
    return json.dumps(metrics.aggregate())


class MetricsRequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the metrics UI server."""

    def do_GET(self) -> None:
        """Handle GET requests for / (HTML) and /api/metrics (JSON)."""
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(INDEX_HTML.encode("utf-8"))
        elif self.path == "/api/metrics":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(render_metrics_json().encode("utf-8"))
        else:
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Not found")

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress default logging."""
        pass


def main() -> None:
    """Start the metrics UI server."""
    parser = argparse.ArgumentParser(
        description="Metrics UI — read-only web dashboard for multi-model-flow metrics"
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind to (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Port to bind to (default: 8765)",
    )
    args = parser.parse_args()

    server_address = (args.host, args.port)
    httpd = HTTPServer(server_address, MetricsRequestHandler)
    url = f"http://{args.host}:{args.port}"
    print(f"Metrics UI server running at {url}")
    print("Press Ctrl+C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        httpd.server_close()


if __name__ == "__main__":
    main()
