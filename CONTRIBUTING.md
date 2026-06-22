# Contributing to multi-model-flow

Thanks for helping improve the plugin. This is a Claude Code plugin: a
slash-command skill (`skills/mmf/SKILL.md`), three sub-agents (`agents/`), and a
bundled Ollama MCP server (`mcp/`, wired via `.mcp.json`).

## Prerequisites

- **Python 3.10+** — `fastmcp` (the MCP server framework) requires it. The
  bundled `mcp/launch.sh` probes `python3.13 … python3.10` then `python3`.
- **Node.js 16+** — only for the optional `llm-checker` model recommender.
- **Ollama** — optional, for local-model offload.

## Setup

```bash
git clone https://github.com/Adam-Luciano-MDB/multi-model-flow.git
cd multi-model-flow
python3.11 -m pip install -r mcp/requirements.txt -r requirements-dev.txt
```

For an end-to-end install (registers the plugin globally), run
`./scripts/setup_mcp.sh --global` and see the README.

## Running tests

```bash
python3.11 -m pytest tests/ -v      # or any Python 3.10+
```

All tests must pass before a PR is merged; CI runs the suite on the supported
Python range.

## Conventions

- **Write tests for new logic.** New behavior in `mcp/*.py` needs matching tests
  in `tests/`. Match the style already in the test files (mock `httpx` and
  `metrics.append`; assert real return values, not just that code ran).
- **Match surrounding conventions** in any file you touch.
- **Skill/agent prompt changes** (`skills/`, `agents/`) are orchestration logic —
  keep them internally consistent with the README architecture diagram, the cost
  model table, and the JSON contracts. Update all of them together.
- **MCP tools must fail gracefully** — return an `"ERROR: ..."` string rather than
  raising, so a tool call never crashes the workflow.
- **Keep the docs in sync.** The README, SKILL.md, and agent files describe the
  same flow from different angles; a behavior change usually touches all three.

## Commit / PR

- Keep commits focused; describe the *why*, not just the *what*.
- Note any user-facing change in `CHANGELOG.md` under `[Unreleased]`.
