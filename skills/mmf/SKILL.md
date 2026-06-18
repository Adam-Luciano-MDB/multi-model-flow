---
name: mmf
description: Planner → Worker → Reviewer pipeline for cost-optimised coding. Opus plans, Haiku implements (+ local Ollama if available), Sonnet reviews.
argument-hint: <task description> [auto] [model:<ollama-model>]
allowed-tools: [Agent, TodoWrite]
---

Run the **multi-model-flow** three-phase pipeline for this task:

> $ARGUMENTS

Parse the arguments:
- **task** — the full text, minus any flags below
- **auto** — skip high-risk confirmation if `[auto]` appears anywhere in the text
- **pinnedModel** — the model name inside `[model:X]` if present (e.g. `[model:devstral:latest]`)

Make a numbered todo list covering all four phases before you start, then tick off each item as you complete it.

---

## Phase 0 — Startup & Ollama probe

1. Spawn a **Haiku agent** to call `ollama-local open_metrics_dashboard` (default port 8765). Log the returned URL so the user can open it. If the tool is unavailable, skip silently.

2. Log a startup banner so the user can see what's configured:
   ```
   mmf (multi-model-flow) | planner: opus | worker: haiku | reviewer: sonnet
   Metrics dashboard: http://localhost:8765 (auto-started above)
   ```

3. Spawn a **Haiku agent** whose sole job is to call the `ollama-local list_local_models` MCP tool and return the raw result.

4. Determine OLLAMA_MODEL from that result:
   - **If a pinnedModel was passed**: if the result confirms Ollama is reachable (any non-ERROR line returned), use pinnedModel. If Ollama is offline, warn and fall back to null.
   - **Otherwise**: parse the model list (skip lines containing "ERROR" or the literal word "none"). Select with this priority: devstral (any variant) → qwen2.5-coder (any variant) → first model in the list → null.

5. Log the outcome:
   - Ollama available: update the banner — `worker: haiku + OLLAMA_MODEL`
   - Ollama offline: `Ollama not available — Worker will use Haiku for all generation`

6. (Optional) If the user mentioned needing hardware recommendations or model selection, warn them first:
   > ⚠ For an accurate recommendation, stop any loaded Ollama models before running llm-checker — a loaded model reduces available memory and causes llm-checker to suggest smaller models than your hardware can actually run. Run `ollama stop <model>` (check `ollama ps`), then re-run with the `[model:<name>]` flag once you've pulled the recommended model.

   Then spawn a Haiku agent to call `llm-checker recommend` with `category: coding` and surface the top suggestion.

---

## Phase 1 — Plan (Opus)

6. Spawn a **Planner agent** (`agentType: planner`) — the planner agent definition sets model: opus automatically — with this prompt:
   > "You are the planner agent. Decompose this development task into a JSON execution plan. Task: TASK"

   The plan must parse as valid JSON with this shape:
   ```json
   {
     "task_summary": "one sentence",
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
     "review_criteria": ["things the Reviewer must check"]
   }
   ```

7. Log: `Plan ready — N step(s), risk: RISK, confidence: CONFIDENCE/10`

8. **Low-confidence guard** — if `confidence < 7`:
   - Spawn a **Strengthener agent** (`model: fable`; if that returns null, retry with `model: opus`) with this prompt:
     > "The following execution plan was produced with low confidence (N/10). Review it critically: identify ambiguities, fill gaps, and return an improved plan JSON. If the plan is already sound, return it as-is with an updated confidence score. Original plan: PLAN_JSON. Task: TASK"
   - If the response contains valid JSON, replace the plan with it.
   - Warn: `⚠ Low plan confidence (N/10) — plan was strengthened. Review the output carefully.`

9. **High-risk guard** — if `risk_level == "high"`:
   - If auto mode is **off**: print the full plan JSON and stop with:
     `HIGH RISK PLAN — review the plan above and re-run with [auto] to proceed.`
   - If auto mode is **on**: log `HIGH RISK PLAN — auto mode enabled, proceeding.` and continue.

---

## Phase 2 — Execute (Haiku + optional Ollama)

Keep a running list of all files written across all steps.

10. For each step in `plan.steps`, in order:

    **a. Ollama pre-generation** (only if OLLAMA_MODEL is set):
    Spawn a **Haiku agent** that calls `ollama-local ask_local_model_for_code` with:
    - `prompt`: the step instruction
    - `language`: inferred from the target_file extension using this map — `.py`→Python, `.ts`/`.tsx`→TypeScript, `.js`/`.jsx`→JavaScript, `.go`→Go, `.rs`→Rust, `.java`→Java, `.rb`→Ruby, `.sh`→Bash, `.sql`→SQL, `.html`→HTML, `.css`→CSS
    - `model`: OLLAMA_MODEL

    If the result does not start with "ERROR", store it as `ollamaContext` for the next sub-step.

    **b. Worker** — Spawn a **Worker agent** (`agentType: worker`) with this prompt:
    > "You are the worker agent. Execute step STEP_ID from the plan below.
    >
    > Plan JSON: PLAN_JSON
    >
    > Execute ONLY step_id STEP_ID. Read all context_files first, then write the target file.
    > [If ollamaContext exists: ] Ollama (OLLAMA_MODEL) has pre-generated an implementation for this step. Use it as your starting point — adapt imports, style, and conventions to match the existing codebase: OLLAMA_CONTEXT"

    Add any files the worker writes to the tracked list.

    **c. Error check** — if the worker reports `{"error": ...}` or missing context: stop execution, tell the user exactly what context is missing, and ask them to provide it before re-running.

---

## Phase 3 — Review (Sonnet → optional Opus)

11. Spawn a **Reviewer agent** (`agentType: reviewer`) with this prompt:
    > "You are the reviewer agent. Review the files written by the Worker against the plan below.
    >
    > Original Plan JSON: PLAN_JSON
    >
    > Files written by Worker: FILE_LIST
    >
    > Read each file, run the test suite if available, and return your verdict JSON."

12. **Confidence escalation** — if `verdict.confidence < 8`:
    Spawn a second **Reviewer agent** (`model: opus`) with the same prompt plus:
    > "Note: A Sonnet reviewer scored this N/10 confidence. Please give it a thorough independent review and return your own verdict JSON."
    Use the Opus verdict going forward.

13. **Verdict**:
    - `approved` or `approved_with_notes`: log the list of files built, show any non-blocking suggestions, then proceed to Phase 4.
    - `rejected` with `new_plan_needed: true`: append `verdict.replanning_notes` to the original task description and repeat from **Phase 1**. Maximum **2 replans total** (3 attempts). If the cap is hit, stop and report the blocking issues.
    - `rejected` without `new_plan_needed`: stop and report the blocking issues. Manual intervention required.

---

## Phase 4 — Metrics

14. Count how many times you spawned each model tier across all phases:
    - **opus**: planner + any Opus strengthener + any Opus review escalation
    - **fable**: any Fable strengthener
    - **sonnet**: reviewer
    - **haiku**: Ollama probe + any Ollama step drivers + workers + metrics call

15. Spawn a **Haiku agent** to call `ollama-local log_event` with:
    - `phase`: `"workflow"`
    - `model`: the tiers actually used, joined with `+` (e.g. `"opus+devstral+haiku+sonnet"`)
    - `outcome`: the final verdict string (e.g. `"approved"`, `"approved_with_notes"`, `"rejected_no_replan"`, `"high_risk"`, `"execution_failed"`)
    - `metadata_json`: a JSON string — `{"task":"<first 80 chars of task>","steps_planned":N,"files_written":N,"retries":N,"ollama_model":"<model or empty string>","claude_calls":{"opus":N,"fable":N,"sonnet":N,"haiku":N}}`

16. Spawn a **Haiku agent** to call the `ollama-local open_metrics_dashboard` tool, then log its returned URL:
    ```
    Done. Metrics dashboard: http://localhost:8765 (read-only). For a text summary, ask: "Use the ollama-local get_metrics_summary tool."
    ```
    If the `open_metrics_dashboard` tool is unavailable (Ollama MCP not installed), fall back to logging:
    `Done. View metrics by running scripts/show_metrics_ui.sh from the plugin directory.`
