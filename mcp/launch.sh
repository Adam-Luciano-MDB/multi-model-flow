#!/usr/bin/env bash
# Launch the Ollama MCP server with a Python that can actually run it.
#
# fastmcp requires Python >=3.10, and the deps (fastmcp, httpx) must be
# importable by whichever interpreter launches the server. A bare `python3`
# is often the system Python (e.g. /usr/bin/python3 = 3.9 on macOS), which is
# both too old and missing the deps. Probe known interpreters newest-first and
# pick the first one that has fastmcp installed.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER="$SCRIPT_DIR/ollama_mcp_server.py"

for py in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$py" >/dev/null 2>&1 && "$py" -c "import fastmcp, httpx" >/dev/null 2>&1; then
    exec "$py" "$SERVER"
  fi
done

echo "ollama-local: no Python >=3.10 with fastmcp+httpx found." >&2
echo "Install deps:  python3.11 -m pip install -r \"$SCRIPT_DIR/requirements.txt\"" >&2
exit 1
