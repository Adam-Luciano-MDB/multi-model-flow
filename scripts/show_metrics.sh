#!/usr/bin/env bash
# Print a summary of workflow and Ollama call metrics from metrics.jsonl.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$SCRIPT_DIR/../mcp/metrics.py"
