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

# 2. Register the Ollama MCP server with Claude Code.
#    Skipped under --global: the plugin ships .mcp.json, which registers
#    ollama-local automatically when the plugin is installed. Registering it
#    here too would create a duplicate server of the same name.
if [ "$GLOBAL" = true ]; then
    echo "[2/3] Skipping manual ollama-local registration — the plugin's .mcp.json"
    echo "      registers it automatically. (Run without --global to register it"
    echo "      standalone for project-mode use.)"
else
    echo "[2/3] Registering ollama-local MCP server with Claude Code..."
    claude mcp add "ollama-local" --transport stdio -- bash "$PROJECT_ROOT/mcp/launch.sh"
    echo "      Done."
fi
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

# Optional: install this clone as a plugin so /mmf works in any project.
# Claude Code discovers plugins through marketplaces, so we register this repo as
# a local marketplace (it ships .claude-plugin/marketplace.json) and install the
# plugin from it. That exposes everything the plugin ships — the skill (skills/),
# the agents (agents/), and the Ollama MCP server (.mcp.json) — in every project.
if [ "$GLOBAL" = true ]; then
    if ! command -v claude >/dev/null 2>&1; then
        echo "[+] Skipping plugin install — 'claude' CLI not found on PATH."
    else
        echo "[+] Registering this repo as a marketplace and installing the plugin..."
        claude plugin marketplace add "$PROJECT_ROOT" 2>&1 | sed 's/^/      /' || true
        claude plugin install multi-model-flow@multi-model-flow 2>&1 | sed 's/^/      /' || true
        echo "      Done — /mmf, its agents, and the ollama-local MCP server are now"
        echo "      available in all Claude Code projects. Restart Claude Code to load them."
        echo "      To update later: git pull, then 'claude plugin marketplace update multi-model-flow'."
    fi
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
