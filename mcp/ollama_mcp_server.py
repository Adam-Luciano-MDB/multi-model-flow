"""
Ollama MCP Server — exposes a local Ollama instance as Claude Code MCP tools.
Fails gracefully when Ollama is offline so the Worker can fall back to Haiku.
"""

import json
import os
import sys
import time
from typing import Optional

import httpx
from fastmcp import FastMCP

# Allow importing the sibling metrics module regardless of working directory.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import metrics as _metrics
import token_usage as _token_usage

OLLAMA_BASE = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
# Optional explicit default model. When unset (the default), tools that need a
# model fall back to the first locally-installed model — no model name is
# hardcoded. Set OLLAMA_DEFAULT_MODEL in your shell only if you want to pin one.
DEFAULT_MODEL = os.environ.get("OLLAMA_DEFAULT_MODEL", "")
TIMEOUT = int(os.environ.get("OLLAMA_TIMEOUT", "1500"))  # seconds — default 25 minutes

# Rough RAM-to-model guidance for coding models. Each entry: (min_gb, model, note).
# Ordered largest-first so the recommender picks the most capable model that fits.
MODEL_GUIDANCE = [
    (48, "qwen2.5-coder:32b", "Best quality; needs a high-RAM machine or GPU."),
    (32, "qwen2.5-coder:14b", "Strong coding model; good balance on 32 GB."),
    (16, "qwen2.5-coder:7b", "Solid 7B coder; comfortable on 16 GB."),
    (8, "qwen2.5-coder:3b", "Lightweight; runs on 8 GB but lower quality."),
    (0, "qwen2.5-coder:1.5b", "Smallest fallback for constrained machines."),
]

mcp = FastMCP("ollama-local")


def _append_metric(record: dict) -> None:
    try:
        _metrics.append(record)
    except Exception:
        pass  # Never let metrics writes crash a tool call


@mcp.tool()
def list_local_models() -> list[str]:
    """Return the names of all models available in the local Ollama instance."""
    try:
        response = httpx.get(f"{OLLAMA_BASE}/api/tags", timeout=10)
        response.raise_for_status()
        data = response.json()
        return [m["name"] for m in data.get("models", [])]
    except httpx.ConnectError:
        return ["ERROR: Ollama is not running. Start it with `ollama serve`."]
    except Exception as e:
        return [f"ERROR: {e}"]


def _first_installed_model() -> str:
    """First locally-installed model name, or "" if none/offline. No hardcoded names."""
    for m in list_local_models():
        if "ERROR" not in m and m.strip().lower() != "none":
            return m
    return ""


def _resolve_model(model: str) -> str:
    """Resolve a possibly-empty model arg: explicit > OLLAMA_DEFAULT_MODEL > first installed."""
    return model or DEFAULT_MODEL or _first_installed_model()


@mcp.tool()
def list_models_for_selection() -> str:
    """
    Return the locally-installed Ollama models as a numbered selection list, so a
    user can pick one to run. The first entry is the default used when no model
    is specified. Returns a guidance message if Ollama is offline or empty.
    """
    models = [m for m in list_local_models() if "ERROR" not in m and m.strip().lower() != "none"]
    if not models:
        errs = [m for m in list_local_models() if "ERROR" in m]
        if errs:
            return errs[0]
        return "No local models installed. Pull one with `ollama pull <model>`."
    lines = ["Installed Ollama models (pick one):"]
    for i, m in enumerate(models, 1):
        suffix = "  ← default (first installed)" if i == 1 else ""
        lines.append(f"  {i}. {m}{suffix}")
    return "\n".join(lines)


@mcp.tool()
def recommend_model() -> str:
    """
    Fallback recommender: suggests a local coding model based on total system
    RAM when llm-checker (Node.js) is not available.

    For GPU-aware recommendations and a catalog of 229+ models, prefer the
    llm-checker MCP server (registered as 'llm-checker') — use its
    recommend tool with category: coding.
    """
    total_gb = _total_ram_gb()
    if total_gb is None:
        return (
            "Could not detect system RAM. Pick a model from this guidance and "
            "pull it with `ollama pull <model>`:\n"
            + "\n".join(f"  - {m} (needs ~{gb}+ GB): {note}" for gb, m, note in MODEL_GUIDANCE)
        )

    pick = next((m for gb, m, note in MODEL_GUIDANCE if total_gb >= gb), MODEL_GUIDANCE[-1][1])
    note = next(note for gb, m, note in MODEL_GUIDANCE if m == pick)

    installed = list_local_models()
    have_pick = not any("ERROR" in m for m in installed) and any(
        m.split(":")[0] == pick.split(":")[0] and pick in m for m in installed
    )

    lines = [
        f"Detected ~{total_gb:.0f} GB total RAM.",
        f"Recommended model: {pick}",
        f"Why: {note}",
        "",
    ]
    if have_pick:
        lines.append(f"'{pick}' appears to be installed already.")
    else:
        lines.append(f"Not installed yet. Pull it with:  ollama pull {pick}")
    lines.append(f"\nSet it as the default:  export OLLAMA_DEFAULT_MODEL={pick}")
    return "\n".join(lines)


def _total_ram_gb() -> Optional[float]:
    """Best-effort total physical RAM in GB across macOS and Linux."""
    try:
        # Available on Linux and macOS without extra deps.
        page_size = os.sysconf("SC_PAGE_SIZE")
        phys_pages = os.sysconf("SC_PHYS_PAGES")
        return (page_size * phys_pages) / (1024**3)
    except (ValueError, OSError, AttributeError):
        return None


@mcp.tool()
def ask_local_model(model: str, prompt: str, system: str = "") -> str:
    """
    Send a prompt to a local Ollama model and return the response text.

    Args:
        model: Ollama model name (e.g. 'qwen2.5-coder:32b').
        prompt: The user prompt.
        system: Optional system prompt.
    """
    model = _resolve_model(model)
    if not model:
        return "ERROR: no model specified and no local models installed. Pull one with `ollama pull <model>`."
    payload: dict = {
        "model": model,
        "prompt": prompt,
        "stream": False,
    }
    if system:
        payload["system"] = system
    t0 = time.time()
    result = ""
    try:
        response = httpx.post(
            f"{OLLAMA_BASE}/api/generate",
            json=payload,
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        result = response.json().get("response", "")
        return result
    except httpx.ConnectError:
        result = "ERROR: Ollama is not running. Start it with `ollama serve` and retry."
        return result
    except httpx.TimeoutException:
        result = f"ERROR: Request timed out after {TIMEOUT}s. The model may need more time or resources."
        return result
    except Exception as e:
        result = f"ERROR: {e}"
        return result
    finally:
        _append_metric({
            "phase": "ollama_call",
            "model": model,
            "outcome": "error" if result.startswith("ERROR") else "success",
            "meta": {
                "prompt_chars": len(prompt),
                "response_chars": len(result),
                "duration_ms": int((time.time() - t0) * 1000),
            },
        })


@mcp.tool()
def ask_local_model_for_code(
    prompt: str,
    context: str = "",
    language: str = "",
    model: str = "",
) -> str:
    """
    Convenience wrapper for code generation via the local Ollama model.

    When `model` is empty, uses OLLAMA_DEFAULT_MODEL if set, otherwise the first
    locally-installed model. No model name is hardcoded. Pass `model` to override.

    Args:
        prompt: What to implement or fix.
        context: Existing file content or surrounding code to consider.
        language: Target programming language (optional but recommended).
        model: Override model name. When empty, resolves to the first installed model.
    """
    model = _resolve_model(model)
    if not model:
        return "ERROR: no local models installed. Pull one with `ollama pull <model>`."

    lang_hint = f" in {language}" if language else ""
    system = (
        f"You are an expert software engineer. "
        f"Generate clean, idiomatic code{lang_hint}. "
        "Output only the requested code with no markdown fences, no explanations, "
        "and no comments beyond what is necessary to understand non-obvious logic. "
        "Match the style and conventions visible in any provided context."
    )

    full_prompt = prompt
    if context:
        full_prompt = f"Context (existing code):\n{context}\n\nTask:\n{prompt}"

    return ask_local_model(model=model, prompt=full_prompt, system=system)


# ── Ollama agentic worker (opt-in; for tool-call-capable models) ──────────────
# File tools handed to the local model so it can drive a step itself — read
# context, write the target file — instead of a Haiku Worker doing it.
_AGENT_TOOLS = [
    {"type": "function", "function": {
        "name": "read_file",
        "description": "Read a file's full contents.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Path relative to the project root."}
        }, "required": ["path"]},
    }},
    {"type": "function", "function": {
        "name": "write_file",
        "description": "Create or overwrite a file with the given content.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Path relative to the project root."},
            "content": {"type": "string", "description": "Full file content to write."}
        }, "required": ["path", "content"]},
    }},
    {"type": "function", "function": {
        "name": "list_files",
        "description": "List entries in a directory.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Directory relative to the project root (default '.')."}
        }, "required": []},
    }},
]


def _safe_join(root: str, path: str) -> str:
    """Resolve `path` under `root`, rejecting traversal outside it."""
    resolved = os.path.normpath(os.path.join(root, path))
    if resolved != root and not resolved.startswith(root + os.sep):
        raise ValueError(f"path '{path}' escapes the work directory")
    return resolved


def _exec_agent_tool(name: str, args, root: str) -> str:
    """Execute one file tool call from the Ollama agent, sandboxed to `root`."""
    if isinstance(args, str):
        try:
            args = json.loads(args or "{}")
        except json.JSONDecodeError:
            return f"ERROR: could not parse arguments: {args!r}"
    if not isinstance(args, dict):
        return f"ERROR: arguments must be an object, got {type(args).__name__}"
    try:
        if name == "read_file":
            with open(_safe_join(root, args["path"])) as fh:
                return fh.read()
        if name == "write_file":
            dest = _safe_join(root, args["path"])
            os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
            with open(dest, "w") as fh:
                fh.write(args.get("content", ""))
            return f"wrote {args['path']}"
        if name == "list_files":
            return "\n".join(sorted(os.listdir(_safe_join(root, args.get("path", ".")))))
        return f"ERROR: unknown tool '{name}'"
    except (KeyError, ValueError, OSError) as e:
        return f"ERROR: {e}"


@mcp.tool()
def run_ollama_coding_agent(
    task: str,
    model: str = "",
    context: str = "",
    work_dir: str = "",
    max_iterations: int = 12,
) -> str:
    """
    Run a tool-calling agentic loop with a local Ollama model so the model itself
    reads context and writes files — replacing the Haiku Worker for a step.

    REQUIRES a model that supports tool/function calling; weaker models will not
    emit tool calls and the run will report that no files were written so the
    caller can fall back to Haiku. File access is sandboxed to `work_dir`.

    Args:
        task: The implementation instruction for this step.
        model: Ollama model; empty resolves to the first installed model.
        context: Optional extra context (e.g. plan JSON, conventions).
        work_dir: Sandbox root for file tools; defaults to the server's cwd.
        max_iterations: Safety cap on tool-calling rounds (default 12).

    Returns a JSON string:
      {"status": "complete|max_iterations|no_tool_calls", "files_written": [...],
       "iterations": N, "final_message": "..."}
    or a string starting with "ERROR:".
    """
    model = _resolve_model(model)
    if not model:
        return "ERROR: no local models installed. Pull one with `ollama pull <model>`."

    root = os.path.abspath(work_dir or os.getcwd())
    system = (
        "You are a coding agent with file tools: read_file, write_file, list_files. "
        "Implement the task by reading any needed context and writing the target "
        "file(s) with write_file. Paths are relative to the project root. When the "
        "task is fully done, reply with a one-line summary and DO NOT call more tools."
    )
    user = task if not context else f"{task}\n\nContext:\n{context}"
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    files_written: list[str] = []
    status = "max_iterations"
    final_message = ""
    t0 = time.time()

    try:
        for _ in range(max_iterations):
            response = httpx.post(
                f"{OLLAMA_BASE}/api/chat",
                json={"model": model, "messages": messages, "tools": _AGENT_TOOLS, "stream": False},
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            msg = response.json().get("message", {}) or {}
            messages.append(msg)
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                final_message = (msg.get("content") or "").strip()
                status = "complete" if files_written else "no_tool_calls"
                break
            for tc in tool_calls:
                fn = tc.get("function", {}) or {}
                name = fn.get("name", "")
                result = _exec_agent_tool(name, fn.get("arguments", {}), root)
                if name == "write_file" and result.startswith("wrote "):
                    rel = result[len("wrote "):]
                    if rel not in files_written:
                        files_written.append(rel)
                messages.append({"role": "tool", "content": result})
    except httpx.ConnectError:
        return "ERROR: Ollama is not running. Start it with `ollama serve` and retry."
    except httpx.HTTPStatusError as e:
        return (f"ERROR: Ollama rejected the request (HTTP {e.response.status_code}). "
                f"The model '{model}' may not support tool calling — use a tool-capable model.")
    except httpx.TimeoutException:
        return f"ERROR: Ollama agent timed out after {TIMEOUT}s."
    except Exception as e:
        return f"ERROR: {e}"
    finally:
        _append_metric({
            "phase": "ollama_agent",
            "model": model,
            "outcome": "error" if not files_written and status == "no_tool_calls" else status,
            "meta": {"files_written": len(files_written), "duration_ms": int((time.time() - t0) * 1000)},
        })

    return json.dumps({
        "status": status,
        "files_written": files_written,
        "iterations": min(max_iterations, len([m for m in messages if m.get("role") == "assistant"]) or max_iterations),
        "final_message": final_message,
    })


@mcp.tool()
def log_event(phase: str, model: str, outcome: str, metadata_json: str = "{}") -> str:
    """
    Record a workflow-level event to the metrics log (metrics.jsonl).

    Called automatically by the multi-model-flow workflow at the end of each run.
    Can also be called manually to annotate runs or record custom events.

    Args:
        phase: e.g. "workflow", "plan", "execute", "review"
        model: model name or tier combo (e.g. "opus+haiku+sonnet")
        outcome: e.g. "approved", "rejected", "high_risk", "execution_failed"
        metadata_json: JSON string with extra fields (task, steps, retries, etc.)
    """
    record: dict = {"phase": phase, "model": model, "outcome": outcome}
    try:
        record["meta"] = json.loads(metadata_json)
    except Exception:
        record["meta"] = {"raw": metadata_json}
    _append_metric(record)
    return f"Logged: phase={phase} model={model} outcome={outcome}"


@mcp.tool()
def get_metrics_summary() -> str:
    """
    Return a human-readable summary of all recorded metrics from metrics.jsonl.

    Shows workflow run outcomes and counts, Ollama call counts and average
    latency by model, and estimated token usage for local calls.
    """
    return _metrics.summarize()


@mcp.tool()
def get_real_token_usage() -> str:
    """
    Return REAL Claude token usage by tier, parsed from Claude Code session
    transcripts (not estimated from call counts).

    Reads the active project's main session plus all sub-agent transcripts,
    sums actual input/output/cache tokens per model tier, and computes real
    cost using current per-tier pricing including prompt-cache multipliers.
    """
    return _token_usage.summarize_real_usage()


def _port_is_open(port: int, host: str = "127.0.0.1", timeout: float = 0.3) -> bool:
    """True if something is already listening on host:port."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


@mcp.tool()
def open_metrics_dashboard(port: int = 8765) -> str:
    """
    Start the read-only metrics dashboard and return its URL.

    Launches the bundled metrics_ui.py as a background process bound to
    127.0.0.1, so it works no matter where the plugin is installed (no need to
    know the install directory). If a server is already listening on the port,
    returns the existing URL instead of starting a second one.

    Args:
        port: Port to serve on (default 8765).
    """
    import subprocess
    import time as _time

    url = f"http://127.0.0.1:{port}"
    if _port_is_open(port):
        return f"Metrics dashboard already running at {url}"

    ui_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "metrics_ui.py")
    if not os.path.exists(ui_script):
        return f"ERROR: metrics_ui.py not found at {ui_script}"

    try:
        subprocess.Popen(
            [sys.executable, ui_script, "--port", str(port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,  # detach so the dashboard outlives this server
        )
    except Exception as e:
        return f"ERROR: could not start dashboard: {e}"

    # Give it a moment to bind so the user doesn't hit connection-refused.
    for _ in range(20):
        if _port_is_open(port):
            break
        _time.sleep(0.1)

    return (
        f"Metrics dashboard started at {url} (read-only, local-only). "
        "Open it in your browser. It keeps running in the background; "
        f"stop it with:  lsof -ti tcp:{port} | xargs kill"
    )


if __name__ == "__main__":
    mcp.run()
