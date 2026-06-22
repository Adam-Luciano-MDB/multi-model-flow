# Security Policy

## Reporting

Found a security issue? Please report it privately to the maintainer
(adam.luciano@mariadb.com) rather than opening a public issue. We'll acknowledge
and work on a fix as quickly as we can.

## What to know about this plugin

multi-model-flow runs local models and can write files. Two behaviors are worth
understanding before you enable them:

- **`[ollama-agent]` writes files directly.** In this mode the local Ollama model
  drives a step through its own tool-calling loop and writes files to disk. Writes
  are **sandboxed to the project working directory** — `_safe_join` resolves paths
  with `realpath` and rejects anything (including symlinked components) that
  escapes the sandbox. However, these writes **bypass Claude Code's per-write
  approval prompts**. The mode is **opt-in** for exactly this reason; only use it
  with models you trust on tasks scoped to a project you control.

- **The metrics dashboard is a local HTTP server.** `open_metrics_dashboard`
  spawns a read-only server bound to `127.0.0.1:8765` (loopback only — not exposed
  to the network). The page is built from your own `metrics.jsonl`; user-controlled
  fields (task text, model names, outcomes) are HTML-escaped before rendering.

- **MCP tools execute locally with your permissions.** The `ollama-local` server
  runs as a subprocess on your machine. Review `mcp/ollama_mcp_server.py` before
  trusting it in a sensitive environment.

## Good practice

- Run `[ollama-agent]` and the demo in a scratch directory first.
- Keep the auto-mode permission grant for `open_metrics_dashboard` scoped
  intentionally (see the README "Auto-launch blocked under auto-approval mode").
- `metrics.jsonl` may contain task descriptions — it is gitignored; don't commit it.
