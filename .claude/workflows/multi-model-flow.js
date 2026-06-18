export const meta = {
  name: "multi-model-flow",
  description: "Three-phase Planner → Worker → Reviewer workflow for cost-optimised coding tasks",
  phases: [
    { title: "Plan", detail: "Opus planner decomposes the task; Fable or Opus validates if confidence < 7" },
    { title: "Execute", detail: "Haiku worker executes each step sequentially" },
    { title: "Review", detail: "Sonnet reviewer verifies correctness; Opus escalates if confidence < 8" },
  ],
}

// args: { task: string, auto?: boolean, ollamaModel?: string }
//   auto        — skip high-risk confirmation halt
//   ollamaModel — pin a specific Ollama model; skips the auto-probe
const taskDescription = (args && args.task) ? args.task : args
const autoMode = !!(args && args.auto)
const pinnedOllamaModel = (args && args.ollamaModel) ? String(args.ollamaModel) : null

const MAX_RETRIES = 2
let retryCount = 0
let currentTask = taskDescription

// Metrics accumulators — set throughout the loop, flushed once at the end.
// Date.now() is unavailable in workflow scripts; timestamps are added by the
// metrics writer on the Python side.
let runOutcome = "halted"
let runStepsPlanned = 0
let runFilesWritten = []
let runRetries = 0

// Infer language from file extension for the Ollama code prompt.
const EXT_LANG = { py: "Python", ts: "TypeScript", tsx: "TypeScript", js: "JavaScript",
  jsx: "JavaScript", go: "Go", rs: "Rust", java: "Java", rb: "Ruby", sh: "Bash",
  sql: "SQL", html: "HTML", css: "CSS" }

// Probe for Ollama once before the retry loop — availability doesn't change between retries.
let ollamaModel = null
if (pinnedOllamaModel) {
  // Verify Ollama is reachable before committing to the pin — otherwise every
  // step fires a Haiku call that silently fails with no benefit.
  const pingText = await agent(
    "Call the ollama-local list_local_models tool. Return only \"ok\" if Ollama responds (even if the list is empty), or \"offline\" if it cannot connect.",
    { label: "ollama:probe", phase: "Execute", model: "haiku" }
  )
  // Fail safe: only commit to the pin on an affirmative "ok". A null/empty/error
  // ping means reachability is unconfirmed — fall back rather than fire a failing
  // Ollama call on every step.
  if (pingText && /\bok\b/i.test(pingText) && !/offline/i.test(pingText)) {
    ollamaModel = pinnedOllamaModel
    log(`Ollama model pinned by caller — using ${ollamaModel}`)
  } else {
    log(`⚠ WARNING: Could not confirm Ollama is reachable — ignoring pinned model ${pinnedOllamaModel}, falling back to Haiku-only`)
  }
} else {
  const probeText = await agent(
    "Call the ollama-local list_local_models tool. Return the model names exactly as given, one per line, or the single word \"none\" if the list is empty or Ollama is not running.",
    { label: "ollama:probe", phase: "Execute", model: "haiku" }
  )
  if (probeText) {
    const available = probeText.trim().split("\n")
      .map(l => l.replace(/^\s*(?:[•\-*]|\d+[.)])\s*/, "").trim())
      .filter(l => l && !/error/i.test(l) && l.toLowerCase() !== "none")
    // Prefer devstral (multi-language expert), then best qwen2.5-coder, then first available
    const devstral = available.find(m => /devstral/i.test(m))
    const qwen = available.find(m => /qwen2\.5-coder/i.test(m))
    ollamaModel = devstral || qwen || available[0] || null
    if (ollamaModel) log(`Ollama auto-detected — ${ollamaModel} will assist with code generation`)
  }
  if (!ollamaModel) log("Ollama not available — Worker will use Haiku for all generation")
}

while (retryCount <= MAX_RETRIES) {
  // ─── Phase 1: Plan ───────────────────────────────────────────────────────

  phase("Plan")
  log(`Planning task (attempt ${retryCount + 1}/${MAX_RETRIES + 1}): ${currentTask}`)

  const planText = await agent(
    `You are the planner agent. Decompose this development task into a JSON execution plan.\n\nTask: ${currentTask}`,
    { label: "planner", phase: "Plan", agentType: "planner" }
  )

  let plan
  try {
    const jsonMatch = planText.match(/\{[\s\S]*\}/)
    if (!jsonMatch) throw new Error("No JSON found in planner output")
    plan = JSON.parse(jsonMatch[0])
  } catch (e) {
    log(`ERROR: Planner did not return valid JSON. Raw output:\n${planText}`)
    runOutcome = "plan_parse_error"
    break
  }

  runStepsPlanned = plan.steps.length
  const planConfidence = typeof plan.confidence === "number" ? plan.confidence : 10
  log(`Plan ready — ${plan.steps.length} step(s), risk: ${plan.risk_level}, confidence: ${planConfidence}/10`)

  // ── Plan confidence check ────────────────────────────────────────────────
  // If Opus scored its own plan below 7, ask Fable to strengthen it.
  // Fall back to an Opus self-validation pass if Fable is unavailable.
  // Never halts the workflow — uses the best plan available and warns the user.
  if (planConfidence < 7) {
    log(`⚠ LOW PLAN CONFIDENCE (${planConfidence}/10) — attempting to strengthen the plan`)

    const strengthenPrompt = `The following execution plan was produced with low confidence (${planConfidence}/10).
Review it critically: identify ambiguities, fill gaps, and return an improved plan JSON.
If the plan is already sound, return it as-is with an updated confidence score.

Original plan:
${JSON.stringify(plan, null, 2)}

Task: ${currentTask}`

    // Try Fable first; agent() returns null when the model is unavailable.
    let strengthenedText = await agent(strengthenPrompt, {
      label: "planner:fable",
      phase: "Plan",
      model: "fable",
    })

    if (!strengthenedText) {
      log(`Fable unavailable — asking Opus to self-validate the plan`)
      strengthenedText = await agent(strengthenPrompt, {
        label: "planner:opus-validate",
        phase: "Plan",
        model: "opus",
      })
    }

    if (strengthenedText) {
      try {
        const jsonMatch = strengthenedText.match(/\{[\s\S]*\}/)
        if (!jsonMatch) throw new Error("no JSON")
        const strengthened = JSON.parse(jsonMatch[0])
        plan = strengthened
        runStepsPlanned = plan.steps.length
        log(`Plan strengthened (new confidence: ${plan.confidence ?? "n/a"}/10)`)
      } catch {
        log(`WARNING: Could not parse strengthened plan — using original`)
      }
    }

    log(`⚠ NOTE: Initial plan confidence was low (${planConfidence}/10). Review the output carefully.`)
  }

  if (plan.risk_level === "high" && !autoMode) {
    log(
      `HIGH RISK PLAN — please review before proceeding:\n${JSON.stringify(plan, null, 2)}\n\nRe-invoke with { auto: true } (or run the auto demo) to proceed without confirmation.`
    )
    runOutcome = "high_risk"
    break
  }
  if (plan.risk_level === "high" && autoMode) {
    log(`HIGH RISK PLAN — auto mode enabled, proceeding without confirmation.`)
  }

  // ─── Phase 2: Execute ────────────────────────────────────────────────────

  phase("Execute")

  const allFilesWritten = []
  let executionFailed = false

  for (const step of plan.steps) {
    log(`Executing step ${step.step_id}/${plan.steps.length}: ${step.action} → ${step.target_file}`)

    // If Ollama is available, ask it to pre-generate code for this step.
    // The Worker receives this as a starting point — it still reads context
    // files, adapts style, and writes the final file.
    let ollamaContext = ""
    if (ollamaModel) {
      const _fname = (step.target_file.split("/").pop() || step.target_file)
      const _dot = _fname.lastIndexOf(".")
      const ext = _dot > 0 ? _fname.slice(_dot + 1).toLowerCase() : ""
      const language = EXT_LANG[ext] || (ext ? ext : "code")
      const contextHint = (step.context_files || []).join(", ") || "none"
      const ollamaText = await agent(
        `Use the ollama-local ask_local_model_for_code tool with these arguments:\n- prompt: "${step.instruction.replace(/"/g, "'")}"\n- context: "Context files to be aware of: ${contextHint}"\n- language: "${language}"\n- model: "${ollamaModel}"\nReturn only the raw output from the tool.`,
        { label: `ollama:step-${step.step_id}`, phase: "Execute", model: "haiku" }
      )
      if (ollamaText && !/^error/i.test(ollamaText)) {
        ollamaContext = `\n\nOllama (${ollamaModel}) has pre-generated an implementation for this step. Use it as your starting point — adapt imports, style, and conventions to match the existing codebase:\n\n${ollamaText}`
      }
    }

    const workerPrompt = `You are the worker agent. Execute step ${step.step_id} from the plan below.

Plan JSON:
${JSON.stringify(plan, null, 2)}

Execute ONLY step_id ${step.step_id}. Read all context_files first, then write the target file.${ollamaContext}`

    const workerOutput = await agent(workerPrompt, {
      label: `worker:step-${step.step_id}`,
      phase: "Execute",
      agentType: "worker",
    })

    // Check for error JSON
    const errorMatch = workerOutput.match(/\{"error"\s*:/)
    if (errorMatch) {
      let errorObj
      try {
        const errorJson = workerOutput.match(/\{[\s\S]*\}/)
        errorObj = errorJson ? JSON.parse(errorJson[0]) : { error: "unknown", needed: workerOutput }
      } catch {
        errorObj = { error: "parse_error", needed: workerOutput }
      }
      log(`Worker stopped at step ${step.step_id}: missing context — ${errorObj.needed}`)
      executionFailed = true
      break
    }

    // Parse completion JSON
    const completionMatch = workerOutput.match(/\{[\s\S]*"status"\s*:\s*"complete"[\s\S]*\}/)
    if (completionMatch) {
      try {
        const completion = JSON.parse(completionMatch[0])
        allFilesWritten.push(...(completion.files_written || []))
        log(`Step ${step.step_id} complete — wrote: ${(completion.files_written || []).join(", ")}`)
      } catch {
        log(`Step ${step.step_id}: could not parse completion JSON, continuing`)
      }
    } else {
      log(`Step ${step.step_id}: worker finished (no structured completion JSON)`)
    }
  }

  runFilesWritten = [...allFilesWritten]

  if (executionFailed) {
    log("Execution halted due to missing context. Fix the context and re-run.")
    runOutcome = "execution_failed"
    break
  }

  // ─── Phase 3: Review ─────────────────────────────────────────────────────

  phase("Review")
  log(`Reviewing ${allFilesWritten.length} file(s): ${allFilesWritten.join(", ")}`)

  const reviewerPrompt = `You are the reviewer agent. Review the files written by the Worker against the plan below.

Original Plan JSON:
${JSON.stringify(plan, null, 2)}

Files written by Worker:
${allFilesWritten.join("\n")}

Read each file, run the test suite if available, and return your verdict JSON.`

  const reviewText = await agent(reviewerPrompt, {
    label: "reviewer:sonnet",
    phase: "Review",
    agentType: "reviewer",
  })

  let verdict
  try {
    const jsonMatch = reviewText.match(/\{[\s\S]*\}/)
    if (!jsonMatch) throw new Error("No JSON found in reviewer output")
    verdict = JSON.parse(jsonMatch[0])
  } catch (e) {
    log(`ERROR: Reviewer did not return valid JSON. Raw output:\n${reviewText}`)
    runOutcome = "review_parse_error"
    break
  }

  const confidence = typeof verdict.confidence === "number" ? verdict.confidence : 10
  log(`Sonnet confidence: ${confidence}/10`)

  // Escalate to Opus when Sonnet's confidence is below 8
  if (confidence < 8) {
    log(`Confidence ${confidence} < 8 — escalating review to Opus`)
    const opusReviewText = await agent(
      `${reviewerPrompt}\n\nNote: A Sonnet reviewer scored this ${confidence}/10 confidence. Please give it a thorough independent review and return your own verdict JSON.`,
      { label: "reviewer:opus", phase: "Review", model: "opus" }
    )
    try {
      const jsonMatch = opusReviewText.match(/\{[\s\S]*\}/)
      if (!jsonMatch) throw new Error("No JSON found in Opus reviewer output")
      verdict = JSON.parse(jsonMatch[0])
      log(`Opus review complete (confidence: ${verdict.confidence ?? "n/a"}/10)`)
    } catch (e) {
      log(`WARNING: Opus reviewer did not return valid JSON — keeping Sonnet verdict`)
    }
  }

  if (verdict.verdict === "approved" || verdict.verdict === "approved_with_notes") {
    log(`\n✓ APPROVED (${verdict.verdict})`)
    log(`Files built:\n${allFilesWritten.map(f => `  • ${f}`).join("\n")}`)
    if (verdict.suggestions && verdict.suggestions.length > 0) {
      log(`\nSuggestions (non-blocking):\n${verdict.suggestions.map(s => `  - ${s}`).join("\n")}`)
    }
    runOutcome = verdict.verdict
    break
  }

  // Rejected
  log(`\n✗ REJECTED`)
  if (verdict.blocking_issues && verdict.blocking_issues.length > 0) {
    log(`Blocking issues:\n${verdict.blocking_issues.map(i => `  • ${i}`).join("\n")}`)
  }

  if (!verdict.new_plan_needed) {
    log("Reviewer did not request replanning. Manual intervention required.")
    runOutcome = "rejected_no_replan"
    break
  }

  retryCount++
  runRetries = retryCount
  if (retryCount > MAX_RETRIES) {
    log(`Reached retry cap (${MAX_RETRIES}). Stopping. Review blocking issues above.`)
    runOutcome = "retry_cap_reached"
    break
  }

  log(`\nReplanning (attempt ${retryCount + 1}) — notes: ${verdict.replanning_notes}`)
  currentTask = `${taskDescription}\n\nReplanning notes from previous attempt:\n${verdict.replanning_notes}`
}

// Flush one workflow-level metrics record via the ollama-local MCP tool.
// Uses Haiku (cheap, fast) and silently no-ops if the MCP server is unavailable.
const taskPreview = String(taskDescription).slice(0, 80).replace(/["'`\\]/g, " ").replace(/\s+/g, " ").trim()
const modelsUsed = ollamaModel ? `opus+${ollamaModel}+haiku+sonnet` : "opus+haiku+sonnet"
const metaJson = JSON.stringify({ task: taskPreview, steps_planned: runStepsPlanned, files_written: runFilesWritten.length, retries: runRetries, ollama_model: ollamaModel || "" })
await agent(
  `Use the ollama-local MCP tool log_event now. Pass these exact argument values:\n- phase: "workflow"\n- model: "${modelsUsed}"\n- outcome: "${runOutcome}"\n- metadata_json: ${metaJson}`,
  { label: "metrics:workflow", model: "haiku" }
)

return { done: true }
