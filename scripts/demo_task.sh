#!/usr/bin/env bash
set -euo pipefail

DEMO_TASK="Create a small utility module with a function that parses a CSV string into a list of records, plus unit tests for it."

echo "=== multi-model-flow: Demo ==="
echo ""
echo "This demo runs the full three-phase skill on a safe, self-contained task:"
echo ""
echo "  Task: $DEMO_TASK"
echo ""
echo "What will happen:"
echo "  Phase 1 (Plan)    — Opus decomposes the task into a JSON execution plan"
echo "  Phase 2 (Execute) — Haiku writes the module and tests step by step"
echo "  Phase 3 (Review)  — Sonnet verifies correctness and style"
echo ""
echo "This is the AUTONOMOUS path: it runs unattended via 'claude --print' and"
echo "passes auto mode so it won't pause even on a high-risk plan."
echo "For a hands-on run, use the interactive slash command instead (see README):"
echo "  /mmf $DEMO_TASK"
echo ""
echo "Starting in 3 seconds... (Ctrl-C to cancel)"
sleep 3
echo ""

claude --print \
  "Use the mmf skill in auto mode on this task: $DEMO_TASK"
