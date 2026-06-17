"""
Ollama MCP Server — exposes a local Ollama instance as Claude Code MCP tools.
Fails gracefully when Ollama is offline so the Worker can fall back to Haiku.
"""

import os

import httpx
from fastmcp import FastMCP

OLLAMA_BASE = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
# Developer-overridable default model. Set OLLAMA_DEFAULT_MODEL in your shell or
# in the MCP server registration to pick the model that suits your hardware.
DEFAULT_MODEL = os.environ.get("OLLAMA_DEFAULT_MODEL", "qwen2.5-coder:32b")
TIMEOUT = int(os.environ.get("OLLAMA_TIMEOUT", "120"))  # seconds — long jobs

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


def _total_ram_gb() -> float | None:
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
    try:
        response = httpx.post(
            f"{OLLAMA_BASE}/api/generate",
            json=payload,
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        return response.json().get("response", "")
    except httpx.ConnectError:
        return "ERROR: Ollama is not running. Start it with `ollama serve` and retry."
    except httpx.TimeoutException:
        return f"ERROR: Request timed out after {TIMEOUT}s. The model may need more time or resources."
    except Exception as e:
        return f"ERROR: {e}"


@mcp.tool()
def ask_local_model_for_code(
    prompt: str,
    context: str = "",
    language: str = "",
) -> str:
    """
    Convenience wrapper for code generation via the local Ollama model.

    Automatically selects qwen2.5-coder:32b (falls back to devstral if
    available) and applies a code-focused system prompt.

    Args:
        prompt: What to implement or fix.
        context: Existing file content or surrounding code to consider.
        language: Target programming language (optional but recommended).
    """
    # Prefer devstral when available for code tasks
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


if __name__ == "__main__":
    mcp.run()
