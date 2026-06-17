# Planner-Worker-Reviewer

[![tests](https://github.com/Adam-Luciano-MDB/multi-model-flow/actions/workflows/test.yml/badge.svg)](https://github.com/Adam-Luciano-MDB/multi-model-flow/actions/workflows/test.yml)

A three-agent Claude Code workflow that routes bulk implementation work to cheap
models (Haiku / local Ollama) while reserving Opus for planning and high-stakes
review. Both the plan and the review carry confidence scores:

- **Plan (Opus)** — if confidence < 7/10, Fable refines the plan (or Opus
  self-validates if Fable is unavailable). The workflow never halts; it warns
  you and continues with the best available plan.
- **Review (Sonnet)** — if confidence < 8/10, Opus is called for an independent
  second opinion before the verdict is accepted.

Drop it into any codebase; it is framework and language agnostic.

---

## Quickstart

```bash
# 1. Install MCP servers + dependencies
./scripts/setup_mcp.sh

# 2. Restart Claude Code, then in an interactive session run the demo task:
#    (no Ollama model required — Worker falls back to Haiku)
```

In Claude Code, type:

```
/dev-task-workflow Create a CSV parser utility with unit tests.
```

You'll watch Opus plan (Fable/Opus validates if plan confidence < 7) → Haiku
build → Sonnet review (escalates to Opus if review confidence < 8). When it
finishes, see what ran and how long it took:

```bash
./scripts/show_metrics.sh
```

That's the whole loop. Everything below is reference detail.

---

## Architecture

```
User task description
        │
        ▼
┌───────────────┐
│   Planner     │
│   (opus)      │
└───────┬───────┘
        │ confidence score 1–10
        ▼
┌───────────────────────┐
│   confidence ≥ 7?     │
└───────┬───────────────┘
   no ▼                │ yes
┌──────────────┐        │
│ Fable refine │        │        ┌──────────────────┐  files_written  ┌───────────────────┐
│ (or Opus     │        ├───────►│     Worker        │ ───────────────►│  Reviewer         │
│  self-check) │        │        │  (haiku or ollama)│                 │  (sonnet)         │
└──────┬───────┘        │        └────────┬─────────┘                 └─────────┬─────────┘
       │  ⚠ warn user   │             (optional)                      confidence score 1–10
       └────────────────┘                 │                                      │
                                          ▼                           ┌──────────▼──────────┐
                                 ┌────────────────┐                  │   confidence ≥ 8?   │
                                 │   Ollama MCP   │                  └──────────┬──────────┘
                                 │  (local, free) │               no ▼          │ yes
                                 └────────────────┘        ┌───────────────┐    │
                                                            │ Opus escalated│    │
                                                            │    review     │    │
                                                            └───────┬───────┘    │
                                                                    └──────┬─────┘
                                                                           │ verdict JSON
                                                                           ▼
                                                            ┌──────────────────────────┐
                                                            │  metrics.jsonl           │
                                                            │  + web dashboard :8765   │
                                                            └──────────────────────────┘
```

---

## Cost model

| Phase             | Agent    | Model              | When                                                      |
|-------------------|----------|--------------------|-----------------------------------------------------------|
| Plan              | Planner  | `opus`             | Always — ambiguous inputs, cross-file reasoning           |
| Plan (strengthen) | Planner  | `claude-fable-5`   | When Opus plan confidence < 7/10 — refine and fill gaps   |
| Plan (self-check) | Planner  | `opus`             | When Fable unavailable and plan confidence < 7/10         |
| Execute           | Worker   | `haiku`            | Default — deterministic, instruction-following, high volume|
| Execute           | Worker   | Ollama (opt.)      | On-prem / long-running / cost-free generation             |
| Review            | Reviewer | `sonnet`           | Always — quality bar without Opus cost                    |
| Review (escalate) | Reviewer | `opus`             | When Sonnet confidence < 8/10 — independent second opinion|

The agent `model:` frontmatter uses **tier aliases** (`opus`, `sonnet`, `haiku`)
rather than pinned version IDs. Aliases always resolve to the latest model in
that tier, so the workflow keeps working as Anthropic ships new versions — no
edits needed. If you need to pin a specific version for reproducibility, replace
the alias with an exact ID (e.g. `claude-opus-4-8`) in the agent's frontmatter.

Never route planning or routine review to Haiku — the JSON contracts require
reasoning about trade-offs that Haiku handles poorly under ambiguous specs.

---

## Prerequisites

- **Claude Code** installed and authenticated (`claude --version`)
- **Python 3.10+** (for the Ollama MCP server)
- **Node.js 16+** (for llm-checker model recommendations — `node --version`)
- **Ollama** (optional, for local model offload)

### Installing Ollama

```bash
# macOS / Linux (one-liner installer)
curl -fsSL https://ollama.com/install.sh | sh

# macOS via Homebrew
brew install ollama

# Windows — download the installer from https://ollama.com/download
```

After installing, start the Ollama server:

```bash
ollama serve
```

Ollama runs on `http://localhost:11434` by default. You can verify it's up:

```bash
curl http://localhost:11434/api/tags
```

### Finding a model to use

`llm-checker` is an MCP server that scores 229+ Ollama models against your
hardware. It is installed and registered automatically by the setup script:

```bash
./scripts/setup_mcp.sh   # installs llm-checker globally and registers it with Claude Code
```

After running setup and **restarting Claude Code**, ask Claude to recommend
the best model for your hardware:

```
Use the llm-checker recommend tool with category: coding.
```

This returns a ranked list with estimated memory usage and tokens/sec for your
specific CPU, GPU, and RAM. Pull the top-ranked model:

```bash
ollama pull qwen2.5-coder:7b   # replace with the recommended model name
```

See **Model selection with llm-checker** below for the full workflow including
GPU-aware recommendations and keeping models up to date.

---

## Setup

```bash
# 1. Make scripts executable
chmod +x scripts/setup_mcp.sh scripts/demo_task.sh

# 2. Install MCP dependencies and register both MCP servers
#    (requires Python 3.10+ and Node.js 16+)
./scripts/setup_mcp.sh

# 3. (If using Ollama) start Ollama, then find and pull the right model:
ollama serve &
# Ask Claude: "Use the llm-checker recommend tool with category: coding."
# Ask Claude: "Use the llm-checker ollama_pull tool with model: <recommended>"
# See "Model selection with llm-checker" below for the full workflow.

# 4. Restart Claude Code to pick up the new MCP servers

# 5. Edit CLAUDE.md — fill in Project, Tech stack, and Project structure
```

---

## Usage

### Full three-phase workflow (recommended)

In an interactive Claude Code session, invoke it as a slash command:

```
/dev-task-workflow Add a rate-limiting middleware to the /api/v2 routes that
caps requests at 100/minute per IP.
```

The workflow:
1. Opus produces a JSON plan with a **confidence score (1–10)**. If confidence
   is below 7, Fable refines the plan (or Opus self-validates if Fable is
   unavailable). Either way the workflow continues — you are warned in the log
   if confidence was low.
2. **Pauses for your confirmation if `risk_level` is `"high"`** (skip with
   auto mode)
3. Worker executes each step in order, writing files
4. Sonnet reviews the result and returns a verdict with its own **confidence
   score (1–10)**. If below 8, Opus is called for an independent second review
5. If rejected with `new_plan_needed: true`, the workflow replans and retries
   (capped at 2 retries)

### Autonomous mode (unattended)

To run end-to-end without the high-risk confirmation halt — for CI, scripts, or
when you trust the task — enable auto mode:

```
/dev-task-workflow with auto mode (auto: true): <your task>
```

Or non-interactively from a script (this is what `./scripts/demo_task.sh` does):

```bash
claude --print "Use the dev-task-workflow with auto mode enabled (auto: true) and task: <your task>"
```

In auto mode a high-risk plan is logged and executed instead of halting. Use it
deliberately — the confirmation step exists to catch destructive plans before
any file is written.

### Individual agents (simpler tasks)

When you already know exactly what you want, invoke agents directly:

```
# Just plan — inspect the plan before committing
Use the planner agent: Add pagination to the /users endpoint.

# Just write a specific file
Use the worker agent with this plan JSON and step_id 2: [paste plan JSON]

# Just review a diff
Use the reviewer agent with this plan JSON and these files: [list files]
```

### Ollama MCP tool (local / on-prem generation)

The Worker can delegate generation to your local Ollama instance:

```
Use the ollama-local MCP tool to generate a Go implementation of a
binary search tree. Language: Go.
```

Available tools:
- `recommend_model` — RAM-based fallback recommender (no Node.js required)
- `list_local_models` — see what models are pulled locally
- `ask_local_model(model, prompt, system)` — raw generation
- `ask_local_model_for_code(prompt, context, language)` — code-optimised wrapper

**Choosing and configuring the local model.** The Worker's local model is *not*
hardcoded — set it once via an environment variable and the server picks it up:

| Variable               | Default               | Purpose                                  |
|------------------------|-----------------------|------------------------------------------|
| `OLLAMA_DEFAULT_MODEL` | `qwen2.5-coder:32b`   | Model used when none is passed explicitly |
| `OLLAMA_BASE_URL`      | `http://localhost:11434` | Ollama endpoint                       |
| `OLLAMA_TIMEOUT`       | `120`                 | Generation timeout (seconds)             |

For a more accurate recommendation that accounts for GPU VRAM, quantization,
and a catalog of 229+ models, use the `llm-checker` MCP server instead
(see **Model selection with llm-checker** below).

---

## Demo

```bash
./scripts/demo_task.sh
```

Runs the full workflow on a safe, self-contained task (CSV parser + tests).

---

## Metrics

The workflow records two kinds of events automatically to `metrics.jsonl` in
the project root (gitignored, append-only JSONL):

| Event | Source | What's captured |
|-------|--------|-----------------|
| `ollama_call` | Python MCP server | model, latency (ms), prompt/response size, outcome |
| `workflow` | Workflow JS via `log_event` | task preview, steps planned, files written, retries, verdict |

Each record: `{"ts": <unix float>, "phase": "...", "model": "...", "outcome": "...", "meta": {...}}`

### View a summary

```bash
./scripts/show_metrics.sh
```

Or ask Claude directly:

```
Use the ollama-local get_metrics_summary tool.
```

### Sample output

```
=== Workflow Runs ===
Total:    4
Outcomes: approved=3  approved_with_notes=1
Avg retries per run: 0.3

Recent runs (newest first):
  2026-06-17 09:41 UTC  approved                steps=3 files=3 retries=0
    task: Add rate-limiting middleware to the /api/v2 routes
  2026-06-17 08:15 UTC  approved_with_notes     steps=4 files=4 retries=1
    task: Add pagination to the /users endpoint

=== Ollama Calls ===
Total: 18 calls

  Model                           Calls  Avg latency  Errors
  ----------------------------------------------------------
  devstral:latest                     3        14.1s       0
  qwen2.5-coder:7b                    15         9.3s       0

Approx tokens in:  48,000  (from 192,000 chars)
Approx tokens out: 12,000  (from  48,000 chars)

Note: Anthropic API calls (Opus/Sonnet/Haiku) are not tracked here.
Use the Claude Console for cost reporting on those tiers.
```

**What the metrics tell you:**
- `steps_planned` vs `files_written` — if files < steps, some steps wrote nothing (check worker output)
- `retries` > 0 — task descriptions that caused reviewer rejection; tighten the description or CLAUDE.md
- Ollama `avg latency` — if consistently > 30s, try a smaller quantization or model size
- Ollama `Errors` > 0 — Ollama went offline mid-run; check `ollama serve`

### View metrics in a web dashboard

For interactive exploration of workflow runs and per-model latency metrics, start
the metrics UI server:

```bash
bash scripts/show_metrics_ui.sh
```

By default this launches a read-only dashboard on `http://127.0.0.1:8765`. Pass
`--port <number>` to use a different port.

The dashboard displays:
- **Workflow summary cards** — total runs, outcome distribution, average retries,
  recent task descriptions
- **Outcome breakdown** — doughnut chart of approved vs approved_with_notes vs rejected
- **Per-model performance** — bar chart and table of Ollama model calls, average
  latencies, and error counts
- **Token usage** — approximate tokens consumed in/out (derived from character counts)
- **Recent runs table** — last 10 workflow executions with timestamps, outcomes, and
  file/step counts

The UI is read-only (no writes to metrics.jsonl) and local-only (binds to 127.0.0.1).

---

## Model selection with llm-checker

`llm-checker` is registered as a second MCP server by the setup script. It
inspects your CPU, GPU, RAM, and acceleration backend (Metal/CUDA/ROCm/CPU),
then scores 229+ Ollama models against your actual hardware — far more accurate
than a RAM-only heuristic because it accounts for GPU VRAM, quantization levels,
and model-family quality.

### Step-by-step: find, pull, and configure the best model

**1. Detect your hardware**

```
Use the llm-checker hw_detect tool.
```

Returns CPU, GPU inventory, total RAM, memory bandwidth, and acceleration backend.
Run this once so you know what you're working with.

**2. Get a coding-optimised recommendation**

```
Use the llm-checker recommend tool with category: coding.
```

Returns a ranked list of models your hardware can run today, with estimated
memory usage, tokens/sec, and a quality score. Pick the top-ranked model name.

**3. Check what's already installed**

```
Use the llm-checker ollama_list tool.
```

If the recommended model is already present, skip to step 5.

**4. Pull the recommended model**

```
Use the llm-checker ollama_pull tool with model: qwen2.5-coder:7b.
```

Or from the terminal directly:

```bash
ollama pull qwen2.5-coder:7b
```

**5. Set the model as the Worker's default**

```bash
export OLLAMA_DEFAULT_MODEL=qwen2.5-coder:7b
# Persist it:
echo 'export OLLAMA_DEFAULT_MODEL=qwen2.5-coder:7b' >> ~/.zshrc
```

### Keeping models up to date

New Ollama model versions are published frequently. Run this periodically:

**Sync the catalog** (refreshes the local 229-model SQLite database from Ollama's
live registry — discovers newly published models and updated quantizations):

```
Use the llm-checker sync tool.
```

**Re-check your recommendation** after syncing — a new model may score higher:

```
Use the llm-checker recommend tool with category: coding.
```

**Pull the update** (Ollama always updates `latest` tags in-place):

```bash
ollama pull qwen2.5-coder:7b
```

Update `OLLAMA_DEFAULT_MODEL` if you switch to a different model.

### Other useful llm-checker tools

| Tool | What it does |
|------|-------------|
| `hw_detect` | Full hardware report: CPU, GPU, RAM, acceleration |
| `recommend` | Ranked model list by category (`coding`, `reasoning`, `general`, `multimodal`) |
| `check` | Full compatibility report against all 229 models |
| `installed` | List locally installed models ranked by hardware fit |
| `ollama_list` | Show pulled models with sizes |
| `ollama_pull` | Download a model |
| `ollama_run` | Run a prompt against a local model with token/sec metrics |
| `ollama_remove` | Delete a model |
| `sync` | Refresh model catalog from Ollama registry |
| `benchmark` | Speed and efficiency test across models |
| `compare_models` | Head-to-head comparison of two models on your hardware |
| `gpu_plan` | Multi-GPU placement advice |
| `smart_recommend` | Best single model for a one-line task description |

---

## JSON contracts

All inter-agent communication is structured JSON. Keep these schemas stable.

### Planner output — Execution Plan

```json
{
  "task_summary": "one sentence describing the task and any assumptions",
  "risk_level": "low|medium|high",
  "confidence": 8,
  "steps": [
    {
      "step_id": 1,
      "action": "create_file|modify_file|write_test|refactor",
      "target_file": "path/to/file",
      "instruction": "precise instruction for the Worker",
      "context_files": ["files the Worker must read first"],
      "validation": "what a correct output looks like"
    }
  ],
  "review_criteria": [
    "human-readable criterion the Reviewer checks"
  ]
}
```

`confidence` is 1–10. If Opus scores its own plan below 7, the workflow
automatically asks Fable to refine it (or Opus to self-validate if Fable is
unavailable). The workflow never halts on low plan confidence — it warns and
continues with the best available plan.

### Worker completion signal

```json
{"step_id": 1, "status": "complete", "files_written": ["path/to/file"]}
```

### Worker error signal

```json
{"error": "missing_context", "needed": "description of what is missing"}
```

### Reviewer verdict

```json
{
  "verdict": "approved|rejected|approved_with_notes",
  "confidence": 9,
  "criteria_results": [
    {
      "criterion": "text from review_criteria",
      "result": "pass|fail|warning",
      "note": "explanation if fail or warning"
    }
  ],
  "blocking_issues": ["must-fix items"],
  "suggestions": ["non-blocking improvements"],
  "new_plan_needed": true,
  "replanning_notes": "what the Planner must change (only when new_plan_needed is true)"
}
```

`confidence` is 1–10. If Sonnet scores below 8, the workflow automatically
escalates to Opus for an independent second review before accepting the verdict.

---

## Customising CLAUDE.md

Open [`CLAUDE.md`](CLAUDE.md) and replace every `<placeholder>` section:

1. **Project** — one sentence about what this codebase does
2. **Tech stack** — languages, frameworks, key libraries
3. **Project structure** — a short directory map (see the comment template)

CLAUDE.md is auto-loaded into every Claude Code session. Good context here
reduces planner mistakes and worker style drift.

---

## Troubleshooting

### Ollama is not running

```
ERROR: Ollama is not running. Start it with `ollama serve`.
```

Run `ollama serve` in a terminal (or configure it as a system service). The
Worker will fall back to Haiku automatically; the Ollama tools just return
error strings rather than raising exceptions.

### MCP server not connecting

1. Check it was registered: `claude mcp list`
2. If missing, re-run `./scripts/setup_mcp.sh`
3. Restart Claude Code after registering

### Reviewer rejects in a loop

The workflow caps retries at 2. If it still fails:
1. Read the `blocking_issues` and `replanning_notes` printed to stdout
2. Fix the underlying ambiguity in your task description or in CLAUDE.md
3. Re-run: `Use the dev-task-workflow with task: [revised description]`

If the reviewer is overly strict for your project, edit
`.claude/agents/reviewer.md` and loosen the blocking criteria.

### Worker outputs markdown fences instead of raw file content

The worker prompt explicitly instructs it not to do this. If a model ignores
the instruction, add this line to `.claude/agents/worker.md`:
```
CRITICAL: Never wrap file output in markdown code fences (``` or ~~~).
```

### High-risk plan halts the workflow

The workflow deliberately stops and prints the plan when `risk_level` is
`"high"`. Review the plan, make any edits to the task description if needed,
then re-invoke to proceed.

### llm-checker MCP not connecting

1. Verify Node.js 16+ is installed: `node --version`
2. Verify llm-checker is installed globally: `npm list -g llm-checker`
3. If missing, install it: `npm install -g llm-checker`
4. Find the MCP server path: `echo "$(npm root -g)/llm-checker/bin/mcp-server.mjs"`
5. Re-register manually:
   ```bash
   claude mcp add "llm-checker" --transport stdio -- \
     node "$(npm root -g)/llm-checker/bin/mcp-server.mjs"
   ```
6. Restart Claude Code.

If Node.js is not available, use the `ollama-local recommend_model` tool as a
fallback — it works without Node.js using RAM-based heuristics.

### Plan confidence is consistently low

If the planner regularly scores below 7, the task descriptions are likely
underspecified. Options:
- Add more context to `CLAUDE.md` (tech stack, file layout, conventions)
- Include specific file paths or function names in your task description
- Break the task into smaller, well-scoped sub-tasks
- Lower the threshold by editing the `planConfidence < 7` check in
  `.claude/workflows/dev-task-workflow.js`

If Fable is unavailable in your Claude Code plan, Opus self-validates instead —
this costs an extra Opus call but produces the same strengthening effect.

### Opus escalation fires on every run

If Sonnet consistently scores below 8, the reviewer prompt may be under-specified
or the tasks you're running are unusually broad. Options:
- Tighten the task description so the scope is clear
- Add more context to `CLAUDE.md` (tech stack, constraints, test conventions)
- Raise the escalation threshold by editing the `confidence < 8` check in
  `.claude/workflows/dev-task-workflow.js`
