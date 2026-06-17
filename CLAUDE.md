# Project Context

## Project
Multi-Model-Flow — a Planner → Worker → Reviewer Claude Code workflow that
routes implementation work to cheap models (Haiku / local Ollama) while
reserving Opus for planning and Sonnet for review.

## Cost model
Planner uses Opus, Reviewer uses Sonnet, Worker uses Haiku or local Ollama.
Do **not** escalate to Opus for routine implementation work. Reserve Opus for
planning (ambiguous requirements, cross-cutting changes) and review of
high-risk diffs.

## Code standards
- Write tests for new logic.
- Match the conventions already present in the files you touch.

<!-- Dropping this into another codebase? Add a one-line Project description,
     your tech stack, and a short directory map here. The agents read this file
     for context — more signal means fewer planning mistakes. -->
