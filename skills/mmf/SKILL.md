---
name: mmf
description: Planner → Worker → Reviewer pipeline for cost-optimised coding. Opus plans, Haiku implements (+ local Ollama if available), Sonnet reviews.
argument-hint: <task description> [auto] [model:<ollama-model>] [ollama-only] [ollama-agent] [fast-select]
allowed-tools: [Agent, TodoWrite, mcp__plugin_multi-model-flow_ollama-local__open_metrics_dashboard]
---

Run the **multi-model-flow** three-phase pipeline for this task:

> $ARGUMENTS

Parse the arguments:
- **task** — the full text, minus any flags below
- **auto** — skip high-risk confirmation if `[auto]` appears anywhere in the text
- **pinnedModel** — the model name inside `[model:X]` if present (e.g. `[model:devstral:latest]`)
- **ollamaOnly** — set if `[ollama-only]` appears; Ollama writes the code, a Haiku agent writes it to the file verbatim (no adaptation)
- **ollamaAgent** — set if `[ollama-agent]` appears; the Ollama model drives the whole step via its own tool-calling loop (reads context, writes files) — no Haiku Worker. Only use with models strong at tool/function calling; falls back to Haiku if the model doesn't call tools
- **fastSelect** — set if `[fast-select]` appears; skips llm-checker scoring at probe time and just uses the first installed model

Make a numbered todo list covering all four phases before you start, then tick off each item as you complete it.

---

## Phase 0 — Startup & Ollama probe

1. Call the `ollama-local open_metrics_dashboard` tool **directly** (it is pre-authorized in this skill's `allowed-tools`, so call it yourself rather than via a sub-agent — this avoids the auto-approval classifier blocking the background-server launch). Default port 8765. Log the returned URL. If the call is still denied or the tool is unavailable, log `Metrics dashboard unavailable — open it later with: Use the ollama-local open_metrics_dashboard tool` and continue.

2. Log a startup banner so the user can see what's configured:
   ```
   mmf (multi-model-flow) | planner: opus | worker: haiku | reviewer: sonnet
   Metrics dashboard: http://localhost:8765 (auto-started above)
   ```

3. Spawn a **Haiku agent** whose sole job is to call the `ollama-local list_local_models` MCP tool and return the raw result.

4. Determine OLLAMA_MODEL from that result. Let INSTALLED = the parsed model list from step 3 (skip lines containing "ERROR" or the literal word "none").

   - **If a pinnedModel was passed**: if Ollama is reachable (any non-ERROR line returned), use pinnedModel. If Ollama is offline, warn and fall back to null. (Skip the scoring below — the user chose explicitly.)
   - **If Ollama is offline or INSTALLED is empty**: OLLAMA_MODEL = null.
   - **Otherwise — scored auto-select** (default; skipped when `[fast-select]` is set):
     1. Spawn a **Haiku agent** to call the `llm-checker recommend` MCP tool with `category: coding`, returning the ranked models with their scores.
     2. Pick the **highest-scored** model whose base name (the part before `:`) matches a model in INSTALLED — i.e. the best *coding-quality* model you already have pulled. Set OLLAMA_MODEL to the installed tag.
     3. Log the candidates considered, e.g.:
        ```
        Ollama model selection (llm-checker, category: coding):
          qwen2.5-coder:7b   score 81  ← selected (installed)
          devstral:latest    score 74  (installed)
          llama3.1:8b        score 40  (installed)
        ```
     - **Note:** the "stop Ollama before running llm-checker" caveat does **not** apply here — this ranks models you've *already* pulled by coding quality, it does not estimate what your hardware can newly run.
   - **Fallback** — use this when `[fast-select]` is set, when `llm-checker` is unavailable or errors, or when no recommended model overlaps INSTALLED: select the **first model in INSTALLED** (or null if empty). No model names are hardcoded.

4b. **Runtime selection** — let the user choose, with the computed pick as the default:
   - **If auto mode is on, a pinnedModel was passed, OLLAMA_MODEL is null, or INSTALLED has only one model**: skip the prompt and use OLLAMA_MODEL as-is.
   - **Otherwise**: present the installed models as a numbered selection list (using the `ollama-local list_models_for_selection` tool, or the candidate log above) with the auto-selected model marked as the recommended default, and ask the user which to use via AskUserQuestion. Offer the recommended model as the first option. If the user picks a different one, set OLLAMA_MODEL to it; if they accept the default or don't answer, keep the computed pick.

5. Log the outcome:
   - Ollama available: update the banner — `worker: haiku + OLLAMA_MODEL`. Then spawn a **Haiku agent** to call `ollama-local get_model_context_length` for OLLAMA_MODEL and append its context window to the banner (e.g. `worker: haiku + granite4:7b-a1b-h (ctx ~1.0M)`) so you and the user know how much the local model can hold. If unknown, omit silently.
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

    **Context-fit pre-check** (only if OLLAMA_MODEL is set): spawn a **Haiku agent** to call `ollama-local estimate_context_fit` with `paths_json` = the step's `context_files`, `model` = OLLAMA_MODEL, and `extra_chars` = length of the step instruction. Read the returned `fits` flag.
    - If `fits` is true (or no context_files) → proceed to the normal path (a0 / a / b below).
    - If `fits` is **false** — the step's context exceeds the Ollama model's window → use the **chunked path with per-step Sonnet check** below instead of a0/a/b.

    **a-chunk. Chunked execution (context overflow)** — Haiku splits the work, Ollama generates each piece, Haiku stitches, Sonnet verifies the step:
    1. Spawn a **Haiku agent** to split the step into an ordered list of sub-tasks, each whose required context (a subset of `context_files`, or one logical section/function group) fits within the OLLAMA_MODEL window reported by `estimate_context_fit`. Track this chunk plan.
    2. For each sub-task in order: spawn a **Haiku agent** that calls `ollama-local ask_local_model_for_code` with only that sub-task's reduced context and instruction. Collect each returned piece.
    3. Spawn a **Haiku agent** to **stitch** the pieces into the complete `target_file` — reconciling imports, removing duplication, ordering definitions — and write the file with the Write tool. Add it to the tracked list.
    4. **Per-step Sonnet check** — spawn a **Reviewer agent** (`agentType: reviewer`, which is Sonnet) scoped to THIS step only:
       > "Verify the file written for step STEP_ID against its instruction. This file was assembled from multiple chunks, so focus on stitch integrity: missing or duplicated definitions, broken or duplicate imports, inconsistent signatures across pieces, and whether the whole satisfies the step instruction. Return a JSON verdict {verdict, issues:[...]}."
       - If the verdict is `approved`/`approved_with_notes`: log `chunked: STEP_ID built from N pieces, Sonnet-checked ✓` and continue to the next step.
       - If `rejected`: spawn a **Haiku agent** to fix the listed issues in the file (one attempt). If a re-check still fails, fall back to a single **Worker agent** (`agentType: worker`) writing the whole `target_file` directly (Haiku, no chunking), and note the fallback.
    5. This per-step Sonnet check is **in addition** to the final Phase 3 review, and only runs for chunked (overflow) steps. Count these Sonnet calls toward the sonnet tier in Phase 4 metrics, and record the chunk count.

    Skip a0/a/b for a step handled by a-chunk.

    **a0. Ollama agentic worker** (only when `ollamaAgent` is set AND OLLAMA_MODEL is set, and the step fit the context window) — the local model does the tool calling itself, replacing the Haiku Worker for this step:
    - Spawn a **Haiku agent** as a thin driver whose only job is to call the `ollama-local run_ollama_coding_agent` MCP tool with:
      - `task`: the step `instruction` plus `target_file` and a note to read any `context_files` first
      - `context`: the plan JSON (or the relevant slice)
      - `model`: OLLAMA_MODEL
    - Parse the returned JSON. If it has a non-empty `context_warning`, surface it to the user (`⚠ <context_warning>`) — the prompt overflowed the model's context window. If `status` is `complete` and `files_written` is non-empty: add those files to the tracked list and log `ollama-agent: OLLAMA_MODEL wrote <files> for step STEP_ID`. Then **skip sub-steps a/b** for this step.
    - If the result starts with `ERROR`, or `status` is `no_tool_calls` (the model didn't call any tools — it isn't tool-capable enough), or no files were written: warn `⚠ [ollama-agent] OLLAMA_MODEL did not complete the step via tool calls — falling back to the Haiku Worker.` and fall through to sub-steps a/b below.
    - This flag is **opt-in** and only appropriate for models strong at tool/function calling. File writes go straight to disk (sandboxed to the project dir), bypassing the Haiku Worker.

    **a. Ollama generation** (only if OLLAMA_MODEL is set, and not already handled by a0):
    Spawn a **Haiku agent** that calls `ollama-local ask_local_model_for_code` with:
    - `prompt`: the step instruction
    - `language`: inferred from the target_file extension using this map — `.py`→Python, `.ts`/`.tsx`→TypeScript, `.js`/`.jsx`→JavaScript, `.go`→Go, `.rs`→Rust, `.java`→Java, `.rb`→Ruby, `.sh`→Bash, `.sql`→SQL, `.html`→HTML, `.css`→CSS
    - `model`: OLLAMA_MODEL

    If the result does not start with "ERROR", store it as `ollamaOutput`.

    **b. Write step** — choose path based on flags:

    - **`ollamaOnly` is set AND `ollamaOutput` exists**: spawn a **Haiku agent** with the sole instruction to write `ollamaOutput` verbatim to `target_file` using the Write tool. No adaptation, no style changes. Log: `ollama-only: wrote OLLAMA_MODEL output directly to TARGET_FILE`.

    - **`ollamaOnly` is set but Ollama was offline or returned an ERROR**: warn `⚠ [ollama-only] Ollama unavailable — falling back to Haiku Worker for step STEP_ID` and continue to the normal Worker below.

    - **Otherwise (normal mode)**: spawn a **Worker agent** (`agentType: worker`) with this prompt:
      > "You are the worker agent. Execute step STEP_ID from the plan below.
      >
      > Plan JSON: PLAN_JSON
      >
      > Execute ONLY step_id STEP_ID. Read all context_files first, then write the target file.
      > [If ollamaOutput exists: ] Ollama (OLLAMA_MODEL) has pre-generated an implementation for this step. Use it as your starting point — adapt imports, style, and conventions to match the existing codebase: OLLAMA_OUTPUT"

    Add any files written to the tracked list.

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
    > Run the test suite (this is a mandatory, blocking gate — capture the real exit code with `; echo \"EXIT:$?\"`). Read each file and return your verdict JSON, including the required `tests` object. If any test fails, the verdict MUST be `rejected`."

12. **Confidence escalation** — if `verdict.confidence < 8`:
    Spawn a second **Reviewer agent** (`model: opus`) with the same prompt plus:
    > "Note: A Sonnet reviewer scored this N/10 confidence. Please give it a thorough independent review and return your own verdict JSON."
    Use the Opus verdict going forward.

13. **Hard test gate (enforced here, not just trusted to the reviewer)** — inspect `verdict.tests` before honoring `verdict.verdict`:
    - If `tests.found` is true AND `tests.ran` is true AND (`tests.exit_code` is non-zero OR `tests.failed > 0`): **treat the run as `rejected` regardless of what `verdict.verdict` says.** Log `✗ Test gate FAILED — N test(s) failing; cannot approve.` with the `tests.output_excerpt`, set `new_plan_needed` true, and use the failing-test output as the replanning note.
    - If `tests.found` is true but `tests.ran` is false (suite exists, couldn't run): do **not** approve — log `✗ Test gate could not run the existing suite — blocking.` and treat as `rejected` (manual intervention).
    - If `tests.found` is false (no suite): log `⚠ No test suite found — nothing to gate on; approving on review alone.` and continue with the reviewer's verdict.
    - If `tests.ran` is true and passed (`exit_code` 0, no failures): log `✓ Test gate passed (N tests)` and continue.

14. **Verdict** (after the test gate):
    - `approved` or `approved_with_notes` (and the test gate did not fail): log the list of files built, show any non-blocking suggestions, then proceed to Phase 4.
    - `rejected` with `new_plan_needed: true` (including a test-gate failure): append `verdict.replanning_notes` (or the failing-test output) to the original task description and repeat from **Phase 1**. Maximum **2 replans total** (3 attempts). If the cap is hit, stop and report the blocking issues (and failing tests).
    - `rejected` without `new_plan_needed`: stop and report the blocking issues. Manual intervention required.

---

## Phase 4 — Metrics

15. Count how many times you spawned each model tier across all phases:
    - **opus**: planner + any Opus strengthener + any Opus review escalation
    - **fable**: any Fable strengthener
    - **sonnet**: final reviewer + one per-step Sonnet check for each chunked (context-overflow) step
    - **haiku**: Ollama probe + any Ollama step drivers + workers + chunk split/stitch/fix agents + metrics call

16. Spawn a **Haiku agent** to call `ollama-local log_event` with:
    - `phase`: `"workflow"`
    - `model`: the tiers actually used, joined with `+` (e.g. `"opus+devstral+haiku+sonnet"`)
    - `outcome`: the final verdict string (e.g. `"approved"`, `"approved_with_notes"`, `"rejected_no_replan"`, `"test_gate_failed"`, `"high_risk"`, `"execution_failed"`)
    - `metadata_json`: a JSON string — `{"task":"<first 80 chars of task>","steps_planned":N,"files_written":N,"retries":N,"chunked_steps":N,"tests_passed":true|false|null,"ollama_model":"<model or empty string>","claude_calls":{"opus":N,"fable":N,"sonnet":N,"haiku":N}}`

17. **Token budget check** — spawn a **Haiku agent** to call `ollama-local check_token_budget` (limit 170000). If it reports any sub-agent over budget, surface the warning to the user verbatim (e.g. `⚠ planner: peak context ~210k tokens — exceeded the 170k budget; consider splitting the task`). The Planner and Reviewer carry the 170k context-budget instruction, but it is guidance the model may exceed; this check verifies it against the real transcript token counts after the run. (A skill cannot hard-cap a sub-agent's context, so this is a check-and-warn, not a hard stop.)

18. Spawn a **Haiku agent** to call the `ollama-local open_metrics_dashboard` tool, then log its returned URL:
    ```
    Done. Metrics dashboard: http://localhost:8765 (read-only). For a text summary, ask: "Use the ollama-local get_metrics_summary tool."
    ```
    If the `open_metrics_dashboard` tool is unavailable (Ollama MCP not installed), fall back to logging:
    `Done. View metrics by running scripts/show_metrics_ui.sh from the plugin directory.`
