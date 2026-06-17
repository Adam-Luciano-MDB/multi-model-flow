#!/usr/bin/env bash
# Start the metrics visualization dashboard server.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$SCRIPT_DIR/../mcp/metrics_ui.py" "$@"
