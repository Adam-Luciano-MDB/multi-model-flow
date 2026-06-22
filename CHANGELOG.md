# Changelog

All notable changes to multi-model-flow are documented here. The format is
loosely based on [Keep a Changelog](https://keepachangelog.com/), and the
project aims to follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- `/mmf` skill (Planner → Worker → Reviewer) packaged as a Claude Code plugin
  with bundled `ollama-local` MCP server, three sub-agents, and a marketplace
  manifest.
- Ollama offload with three Worker modes: default (Haiku adapts an Ollama draft),
  `[ollama-only]` (Haiku writes the draft verbatim), and `[ollama-agent]` (the
  local model drives the step via its own tool-calling loop).
- Smart model selection at probe time (llm-checker coding score → first installed
  model), with `[fast-select]` to skip scoring and a runtime selection prompt.
- Context-window awareness: `get_model_context_length` / `estimate_context_fit`,
  with a chunk-and-stitch path (Haiku splits → Ollama generates → Haiku stitches
  → per-step Sonnet check) when a step's context exceeds the model's window.
- Hard test gate: the reviewer must run the suite; failing tests force `rejected`
  (enforced in Phase 3, not left to the reviewer).
- Tiered test-failure recovery: a Sonnet-guided lighter fix loop (Haiku/Ollama
  applies the fix) before escalating to a full Opus re-plan.
- Metrics: `metrics.jsonl`, a read-only web dashboard (`open_metrics_dashboard`,
  :8765) with cost comparison and per-model token columns, and
  `get_real_token_usage` (actual per-tier tokens parsed from session transcripts).
- 170k per-subtask context budget on Planner/Reviewer, verified post-run via
  `check_token_budget`.
- `mmf-plugin.htm` — a single-page visual reference for the plugin.

[Unreleased]: https://github.com/Adam-Luciano-MDB/multi-model-flow
