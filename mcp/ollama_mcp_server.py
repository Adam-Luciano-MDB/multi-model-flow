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
# Developer-overridable default model. Set OLLAMA_DEFAULT_MODEL in your shell or
# in the MCP server registration to pick the model that suits your hardware.
DEFAULT_MODEL = os.environ.get("OLLAMA_DEFAULT_MODEL", "qwen2.5-coder:32b")
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
    if not model:
        model = DEFAULT_MODEL
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

    Automatically selects the best available model (devstral if installed,
    otherwise DEFAULT_MODEL). Pass `model` to override the selection.

    Args:
        prompt: What to implement or fix.
        context: Existing file content or surrounding code to consider.
        language: Target programming language (optional but recommended).
        model: Override model name. When empty, auto-selects from installed models.
    """
    if not model:
        available = list_local_models()
        model = DEFAULT_MODEL
        if not any("ERROR" in m for m in available):
            for m in available:
                if "devstral" in m.lower():
                    model = m
                    break

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
