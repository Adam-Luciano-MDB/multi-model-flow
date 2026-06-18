#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Parse --global flag
GLOBAL=false
for arg in "$@"; do
  case "$arg" in
    --global) GLOBAL=true ;;
  esac
done

echo "=== Multi-Model-Flow: MCP Setup ==="
echo ""

# 1. Install Python dependencies
echo "[1/3] Installing Ollama MCP server dependencies..."
python3.11 -m pip install -r "$PROJECT_ROOT/mcp/requirements.txt" 2>/dev/null \
  || pip3 install -r "$PROJECT_ROOT/mcp/requirements.txt"
echo "      Done."
echo ""

# 2. Register the Ollama MCP server with Claude Code
echo "[2/3] Registering ollama-local MCP server with Claude Code..."
claude mcp add "ollama-local" --transport stdio -- python "$PROJECT_ROOT/mcp/ollama_mcp_server.py"
echo "      Done."
echo ""

# 3. Install llm-checker and register its MCP server
echo "[3/3] Installing llm-checker (hardware-aware model recommender)..."
if ! command -v node >/dev/null 2>&1; then
    echo "      WARNING: Node.js not found — skipping llm-checker."
    echo "      Install Node.js 16+ from https://nodejs.org and re-run this script"
    echo "      to enable GPU-aware model recommendations."
else
    npm install -g llm-checker
    LLM_CHECKER_MCP="$(npm root -g)/llm-checker/bin/mcp-server.mjs"
    claude mcp add "llm-checker" --transport stdio -- node "$LLM_CHECKER_MCP"
    echo "      Done."
fi
echo ""

# Optional: install workflow + agents globally so /multi-model-flow works in any project
if [ "$GLOBAL" = true ]; then
    echo "[+] Installing /multi-model-flow globally to ~/.claude/ ..."
    mkdir -p ~/.claude/workflows ~/.claude/agents
    cp "$PROJECT_ROOT/.claude/workflows/multi-model-flow.js" ~/.claude/workflows/
    cp "$PROJECT_ROOT/.claude/agents/planner.md"  ~/.claude/agents/
    cp "$PROJECT_ROOT/.claude/agents/worker.md"   ~/.claude/agents/
    cp "$PROJECT_ROOT/.claude/agents/reviewer.md" ~/.claude/agents/
    echo "      Done — /multi-model-flow is now available in all Claude Code projects."
    echo ""
fi

echo "=== Setup complete. Manual steps required: ==="
echo ""
echo "  [ ] 1. Verify Ollama is running:"
echo "         ollama serve"
echo "         (in a separate terminal, or configure it as a background service)"
echo ""
echo "  [ ] 2. Find the best model for your hardware (choose one approach):"
echo ""
echo "         Option A — llm-checker (recommended, GPU-aware):"
echo '         Ask Claude: "Use the llm-checker recommend tool with category: coding."'
echo '         Then: "Use the llm-checker ollama_pull tool with the recommended model."'
echo ""
echo "         Option B — RAM-only fallback (no Node.js required):"
echo '         Ask Claude: "Use the ollama-local recommend_model tool."'
echo "         Then pull what it suggests, e.g.:"
echo "         ollama pull qwen2.5-coder:7b"
echo ""
echo "  [ ] 3. Set your chosen model as the default (so Worker uses it):"
echo "         export OLLAMA_DEFAULT_MODEL=qwen2.5-coder:7b"
echo "         (add to ~/.zshrc or ~/.bashrc to persist)"
echo ""
echo "  [ ] 4. Sync the llm-checker model catalog (optional — gets latest models):"
echo '         Ask Claude: "Use the llm-checker sync tool."'
echo ""
echo "  [ ] 5. Restart Claude Code so the new MCP servers are loaded."
echo ""
echo "  [ ] 6. Edit CLAUDE.md with your project description, tech stack,"
echo "         and directory structure."
echo ""
echo "Run the test suite (Python 3.11+ required):"
echo "  python3.11 -m pytest tests/ -v"
echo ""
echo "Test the MCP connections by asking Claude:"
echo '  "Use the llm-checker hw_detect tool to inspect my hardware."'
echo '  "List my local Ollama models using the ollama-local MCP tool."'
