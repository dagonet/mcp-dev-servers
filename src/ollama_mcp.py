import os
import json
import fnmatch
import httpx
from pathlib import Path
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("ollama-tools")

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")

MODEL_FIRST_PASS = os.environ.get("OLLAMA_MODEL_FIRST_PASS", "mistral:7b-instruct-q4_K_M")
MODEL_EXTRACT_JSON = os.environ.get("OLLAMA_MODEL_EXTRACT_JSON", "qwen2.5:7b-instruct-q4_K_M")

# Shared httpx client for connection pooling (created lazily)
_http_client: httpx.AsyncClient | None = None


async def _get_http_client() -> httpx.AsyncClient:
    """Get or create a shared httpx client for connection pooling."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=120)
    return _http_client


# -------------------------
# Helpers
# -------------------------

async def ollama_generate(prompt: str, model: str, keep_alive: str | int | None = None) -> str:
    """
    Generate a response from Ollama using the specified model.

    Args:
        prompt: The prompt to send to the model
        model: The Ollama model identifier
        keep_alive: How long to keep model loaded ("30m", -1 for forever, etc.)

    Returns:
        The generated text response
    """
    payload = {"model": model, "prompt": prompt, "stream": False}
    if keep_alive is not None:
        payload["keep_alive"] = keep_alive

    client = await _get_http_client()
    r = await client.post(f"{OLLAMA_URL}/api/generate", json=payload)
    r.raise_for_status()
    return r.json().get("response", "")


# -------------------------
# Ollama tools
# -------------------------

@mcp.tool()
async def ollama_health() -> str:
    """
    Check if the Ollama server is reachable and responding.

    Returns:
        JSON with status and version info if available
    """
    try:
        client = await _get_http_client()
        r = await client.get(f"{OLLAMA_URL}/api/version")
        r.raise_for_status()
        version_info = r.json()
        return json.dumps({
            "status": "ok",
            "url": OLLAMA_URL,
            "version": version_info.get("version", "unknown"),
        }, ensure_ascii=False)
    except httpx.ConnectError:
        return json.dumps({
            "status": "error",
            "url": OLLAMA_URL,
            "error": "Connection refused - is Ollama running?",
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "status": "error",
            "url": OLLAMA_URL,
            "error": str(e),
        }, ensure_ascii=False)


@mcp.tool()
async def ollama_list_models() -> str:
    """
    List all models available in Ollama.

    Returns:
        JSON with list of model names and details
    """
    try:
        client = await _get_http_client()
        r = await client.get(f"{OLLAMA_URL}/api/tags")
        r.raise_for_status()
        data = r.json()
        models = []
        for m in data.get("models", []):
            models.append({
                "name": m.get("name"),
                "size": m.get("size"),
                "modified_at": m.get("modified_at"),
            })
        return json.dumps({
            "status": "ok",
            "models": models,
            "count": len(models),
        }, ensure_ascii=False)
    except httpx.ConnectError:
        return json.dumps({
            "status": "error",
            "error": "Connection refused - is Ollama running?",
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "status": "error",
            "error": str(e),
        }, ensure_ascii=False)


@mcp.tool()
async def warm_models(keep_alive: str | int = -1) -> str:
    """
    Pre-load configured models into Ollama for faster first inference.

    Args:
        keep_alive: How long to keep models loaded ("30m", -1 for forever)

    Returns:
        JSON with warmup status for each model
    """
    models = [
        ("first_pass", MODEL_FIRST_PASS),
        ("extract_json", MODEL_EXTRACT_JSON),
    ]
    results = []
    for name, m in models:
        try:
            _ = await ollama_generate("ping", model=m, keep_alive=keep_alive)
            results.append({"tool": name, "model": m, "status": "ok"})
        except Exception as e:
            results.append({"tool": name, "model": m, "status": "error", "error": str(e)})
    return json.dumps({"warmup": results}, ensure_ascii=False)


@mcp.tool()
async def local_first_pass(text: str, goal: str = "implementation plan") -> str:
    prompt = (
        "You are a fast local assistant that compresses information.\n"
        f"GOAL: {goal}\n"
        "Return concise, structured bullets. Preserve filenames, APIs, numbers.\n\n"
        f"TEXT:\n{text}"
    )
    return await ollama_generate(prompt, model=MODEL_FIRST_PASS, keep_alive=-1)


@mcp.tool()
async def extract_json(text: str, schema: str, max_retries: int = 2) -> str:
    expected_keys = None
    try:
        schema_obj = json.loads(schema)
        if isinstance(schema_obj, dict):
            expected_keys = set(schema_obj.keys())
    except Exception:
        pass

    base_prompt = (
        "Return ONLY valid JSON that matches the schema.\n"
        "STRICT RULES:\n"
        "- Output must be ONLY JSON (no markdown, no explanation)\n"
        "- Must include ALL top-level keys from the schema, even if empty\n"
        "- Use double quotes for keys/strings\n"
        "- If unknown, use null or []\n\n"
        f"SCHEMA:\n{schema}\n\n"
        f"TEXT:\n{text}"
    )

    last = ""
    for _ in range(max_retries + 1):
        last = await ollama_generate(base_prompt, model=MODEL_EXTRACT_JSON, keep_alive=-1)
        try:
            obj = json.loads(last)
        except Exception:
            continue

        if expected_keys:
            missing = expected_keys - set(obj.keys())
            if missing:
                continue

        return last

    if expected_keys:
        fallback = {k: [] for k in expected_keys}
        fallback["_note"] = "Model failed to follow schema"
        fallback["_raw"] = last[:2000]
        return json.dumps(fallback, ensure_ascii=False)

    return json.dumps({"error": "invalid_json", "raw": last[:2000]}, ensure_ascii=False)


@mcp.tool()
async def map_project_structure(root: str, include: str = "*", max_files: int = 2000) -> str:
    """
    Map the structure of a project directory.

    Args:
        root: Root directory to scan
        include: Glob pattern to filter files (e.g., "*.cs", "*.py", "*")
        max_files: Maximum number of files to return

    Returns:
        JSON with root path, file count, and sorted file list
    """
    root_path = Path(root).resolve()
    exclude = {".git", ".vs", "bin", "obj", "node_modules", "TestResults", "TestResults_MCP"}

    # Parse include patterns (comma-separated)
    patterns = [p.strip() for p in include.split(",") if p.strip()]
    if not patterns:
        patterns = ["*"]

    files = []
    for p in root_path.rglob("*"):
        if len(files) >= max_files:
            break
        if p.is_dir():
            continue
        if any(part in exclude for part in p.parts):
            continue

        rel = str(p.relative_to(root_path)).replace("\\", "/")

        # Apply include filter
        if patterns != ["*"]:
            filename = p.name
            if not any(fnmatch.fnmatch(filename, pat) for pat in patterns):
                continue

        files.append(rel)

    return json.dumps(
        {"root": str(root_path), "pattern": include, "count": len(files), "files": sorted(files)},
        ensure_ascii=False,
    )


# -------------------------
# Entry point
# -------------------------

if __name__ == "__main__":
    mcp.run(transport="stdio")
