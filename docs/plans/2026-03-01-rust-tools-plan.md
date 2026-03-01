# rust-tools MCP Server Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Create a `rust-tools` MCP server with 4 tools for reliable Cargo command execution on Windows.

**Architecture:** Single file `src/rust_mcp.py` following the exact patterns of `src/git_mcp.py` — same helpers (`_kill_process_tree`, `_SUBPROCESS_FLAGS`), same JSON response format, same executable resolution pattern. Uses Cargo's native `--message-format=json` for structured build/clippy diagnostics.

**Tech Stack:** Python 3.11+, `mcp[cli]` (FastMCP), stdlib `subprocess`/`shutil`/`json`

**Design doc:** `docs/plans/2026-03-01-rust-tools-design.md`

---

### Task 1: Create `src/rust_mcp.py` with boilerplate and helpers

**Files:**
- Create: `src/rust_mcp.py`

**Step 1: Create the server file with FastMCP, subprocess helpers, and cargo executable resolution**

```python
"""
Rust/Cargo MCP Server Tools

Tools for Rust development:
- Environment diagnostics
- Build with structured error output
- Test execution
- Clippy linting with structured diagnostics
"""

import shutil
import os
import json
import time
import subprocess
import signal
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("rust-tools")

# Cross-platform helper for subprocess creation flags
_SUBPROCESS_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


# -------------------------
# Helpers
# -------------------------

def _kill_process_tree(pid: int) -> None:
    """Kill a process and its children, cross-platform."""
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
            )
        except Exception:
            pass
    else:
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                os.kill(pid, signal.SIGKILL)
            except Exception:
                pass


def _cargo_exe() -> str:
    """
    Find the cargo executable path, preferring .exe on Windows.

    Returns:
        Path to cargo executable
    """
    if os.name == "nt":
        p = shutil.which("cargo.exe")
        if p:
            return p
    p = shutil.which("cargo")
    if p:
        return p
    return "cargo.exe" if os.name == "nt" else "cargo"


def run_cmd(cmd: list[str], cwd: str | None = None, timeout: int = 300) -> dict:
    """
    Run a command and capture output (simple subprocess.run wrapper).

    Args:
        cmd: Command and arguments as a list
        cwd: Working directory
        timeout: Maximum execution time in seconds

    Returns:
        Dict with exit_code, stdout, stderr, and duration_s
    """
    start = time.time()
    try:
        p = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            creationflags=_SUBPROCESS_FLAGS,
        )
        return {
            "exit_code": p.returncode,
            "stdout": p.stdout[-200_000:] if p.stdout else "",
            "stderr": p.stderr[-200_000:] if p.stderr else "",
            "duration_s": round(time.time() - start, 3),
        }
    except subprocess.TimeoutExpired:
        return {
            "exit_code": 124,
            "stdout": "",
            "stderr": f"Command timed out after {timeout}s",
            "duration_s": round(time.time() - start, 3),
        }
    except FileNotFoundError as e:
        return {
            "exit_code": 127,
            "stdout": "",
            "stderr": f"Command not found: {e}",
            "duration_s": round(time.time() - start, 3),
        }


def run_cargo(args: list[str], cwd: str, timeout_s: int = 300) -> dict:
    """
    Run cargo safely in stdio MCP context (cross-platform).

    Features:
        - Does not inherit MCP stdin
        - Resolves cargo.exe properly on Windows
        - Enforces timeout by killing process tree

    Args:
        args: Cargo command arguments (without 'cargo' prefix)
        cwd: Working directory (project path)
        timeout_s: Timeout in seconds

    Returns:
        Dict with exit_code, stdout, stderr, timed_out, duration_s, cmd
    """
    env = os.environ.copy()
    env["CARGO_TERM_COLOR"] = "never"

    exe = _cargo_exe()
    cmd = [exe] + args
    start = time.time()

    p = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        shell=False,
        creationflags=_SUBPROCESS_FLAGS,
    )

    try:
        out, err = p.communicate(timeout=timeout_s)
        return {
            "exit_code": p.returncode,
            "stdout": (out or "")[-200_000:],
            "stderr": (err or "")[-200_000:],
            "timed_out": False,
            "duration_s": round(time.time() - start, 3),
            "cmd": cmd,
        }
    except subprocess.TimeoutExpired:
        _kill_process_tree(p.pid)
        return {
            "exit_code": 124,
            "stdout": "",
            "stderr": f"Timed out after {timeout_s}s: {' '.join(args)}",
            "timed_out": True,
            "duration_s": round(time.time() - start, 3),
            "cmd": cmd,
        }


def _parse_cargo_diagnostics(json_output: str) -> list[dict]:
    """
    Parse cargo's --message-format=json output into structured diagnostics.

    Args:
        json_output: Raw stdout from cargo with --message-format=json

    Returns:
        List of diagnostic dicts with level, message, file, line, column
    """
    diagnostics = []
    for line in json_output.splitlines():
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue

        if msg.get("reason") != "compiler-message":
            continue

        inner = msg.get("message", {})
        level = inner.get("level", "")
        message = inner.get("message", "")

        # Find primary span for file location
        file_name = ""
        line_start = 0
        column_start = 0
        for span in inner.get("spans", []):
            if span.get("is_primary", False):
                file_name = span.get("file_name", "")
                line_start = span.get("line_start", 0)
                column_start = span.get("column_start", 0)
                break

        diagnostics.append({
            "level": level,
            "message": message,
            "file": file_name,
            "line": line_start,
            "column": column_start,
        })

    return diagnostics


# -------------------------
# Entry point
# -------------------------

if __name__ == "__main__":
    mcp.run(transport="stdio")
```

**Step 2: Verify the file loads without error**

Run: `python -c "import importlib.util; spec = importlib.util.spec_from_file_location('m', r'src/rust_mcp.py'); mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); print(f'Server: {mod.mcp.name}')"`

Expected: `Server: rust-tools`

**Step 3: Commit**

```bash
git add src/rust_mcp.py
git commit -m "feat: add rust-tools MCP server boilerplate

Helpers: _cargo_exe(), run_cargo(), run_cmd(), _kill_process_tree(),
_parse_cargo_diagnostics(). Same patterns as git_mcp.py."
```

---

### Task 2: Add `cargo_env_info` tool

**Files:**
- Modify: `src/rust_mcp.py` (add tool before entry point section)

**Step 1: Add the tool**

Insert before the `# Entry point` comment:

```python
# -------------------------
# Cargo Tools
# -------------------------

@mcp.tool()
async def cargo_env_info() -> str:
    """
    Return diagnostic information about the Rust/Cargo installation.
    Useful for debugging cargo-related issues.

    Returns:
        JSON with platform, cargo/rustc paths, versions, and tauri CLI status
    """
    info = {
        "platform": os.name,
        "cargo": shutil.which("cargo"),
        "resolved_cargo_exe": _cargo_exe(),
        "rustc": shutil.which("rustc"),
    }

    # Cargo version
    res = run_cmd([_cargo_exe(), "--version"], timeout=10)
    if res["exit_code"] == 0:
        info["cargo_version"] = res["stdout"].strip()

    # Rustc version
    rustc = shutil.which("rustc.exe" if os.name == "nt" else "rustc") or "rustc"
    res = run_cmd([rustc, "--version"], timeout=10)
    if res["exit_code"] == 0:
        info["rustc_version"] = res["stdout"].strip()

    # Rustup default toolchain
    rustup = shutil.which("rustup.exe" if os.name == "nt" else "rustup") or "rustup"
    res = run_cmd([rustup, "show", "active-toolchain"], timeout=10)
    if res["exit_code"] == 0:
        info["active_toolchain"] = res["stdout"].strip()

    # Tauri CLI
    res = run_cmd([_cargo_exe(), "tauri", "--version"], timeout=10)
    if res["exit_code"] == 0:
        info["tauri_cli"] = res["stdout"].strip()
    else:
        info["tauri_cli"] = None

    return json.dumps(info, ensure_ascii=False)
```

**Step 2: Verify tool is registered**

Run: `python -c "import src.rust_mcp as m; print(list(m.mcp._tool_manager._tools.keys()))"`

Expected: `['cargo_env_info']`

**Step 3: Commit**

```bash
git add src/rust_mcp.py
git commit -m "feat(rust-tools): add cargo_env_info diagnostic tool"
```

---

### Task 3: Add `cargo_build` tool

**Files:**
- Modify: `src/rust_mcp.py` (add tool after `cargo_env_info`)

**Step 1: Add the tool**

```python
@mcp.tool()
async def cargo_build(cwd: str, release: bool = False, target: str = "", features: str = "") -> str:
    """
    Build a Rust project and return structured error/warning diagnostics.

    Uses cargo's --message-format=json for machine-readable output.

    Args:
        cwd: Project directory containing Cargo.toml
        release: Build in release mode
        target: Target triple (e.g., "x86_64-pc-windows-msvc")
        features: Comma-separated feature flags

    Returns:
        JSON with exit_code, error/warning counts, diagnostics list, duration_s
    """
    args = ["build", "--message-format=json"]
    if release:
        args.append("--release")
    if target:
        args.extend(["--target", target])
    if features:
        args.extend(["--features", features])

    res = run_cargo(args, cwd=cwd, timeout_s=300)

    diagnostics = _parse_cargo_diagnostics(res["stdout"])
    errors = [d for d in diagnostics if d["level"] == "error"]
    warnings = [d for d in diagnostics if d["level"] == "warning"]

    return json.dumps({
        "exit_code": res["exit_code"],
        "timed_out": res["timed_out"],
        "errors": len(errors),
        "warnings": len(warnings),
        "diagnostics": diagnostics[:200],
        "stderr": res["stderr"],
        "duration_s": res["duration_s"],
        "truncated": len(diagnostics) > 200,
    }, ensure_ascii=False)
```

**Step 2: Verify tool is registered**

Run: `python -c "import src.rust_mcp as m; print(list(m.mcp._tool_manager._tools.keys()))"`

Expected: `['cargo_env_info', 'cargo_build']`

**Step 3: Commit**

```bash
git add src/rust_mcp.py
git commit -m "feat(rust-tools): add cargo_build with JSON diagnostic parsing"
```

---

### Task 4: Add `cargo_test` tool

**Files:**
- Modify: `src/rust_mcp.py` (add tool after `cargo_build`)

**Step 1: Add the tool**

```python
@mcp.tool()
async def cargo_test(cwd: str, test_name: str = "", release: bool = False) -> str:
    """
    Run Rust tests and return results.

    Args:
        cwd: Project directory containing Cargo.toml
        test_name: Optional specific test name or pattern to run
        release: Run tests in release mode

    Returns:
        JSON with exit_code, stdout (test output), stderr, duration_s
    """
    args = ["test"]
    if release:
        args.append("--release")
    if test_name:
        args.extend(["--", test_name])

    res = run_cargo(args, cwd=cwd, timeout_s=300)

    return json.dumps({
        "exit_code": res["exit_code"],
        "timed_out": res["timed_out"],
        "stdout": res["stdout"],
        "stderr": res["stderr"],
        "duration_s": res["duration_s"],
    }, ensure_ascii=False)
```

**Step 2: Verify tool is registered**

Run: `python -c "import src.rust_mcp as m; print(list(m.mcp._tool_manager._tools.keys()))"`

Expected: `['cargo_env_info', 'cargo_build', 'cargo_test']`

**Step 3: Commit**

```bash
git add src/rust_mcp.py
git commit -m "feat(rust-tools): add cargo_test tool"
```

---

### Task 5: Add `cargo_clippy` tool

**Files:**
- Modify: `src/rust_mcp.py` (add tool after `cargo_test`)

**Step 1: Add the tool**

```python
@mcp.tool()
async def cargo_clippy(cwd: str, fix: bool = False) -> str:
    """
    Run clippy linter and return structured diagnostics.

    Uses cargo's --message-format=json for machine-readable output.

    Args:
        cwd: Project directory containing Cargo.toml
        fix: If True, automatically apply suggested fixes

    Returns:
        JSON with exit_code, error/warning counts, diagnostics list, duration_s
    """
    args = ["clippy", "--message-format=json"]
    if fix:
        args.extend(["--fix", "--allow-dirty"])
    args.extend(["--", "-D", "warnings"])

    res = run_cargo(args, cwd=cwd, timeout_s=300)

    diagnostics = _parse_cargo_diagnostics(res["stdout"])
    errors = [d for d in diagnostics if d["level"] == "error"]
    warnings = [d for d in diagnostics if d["level"] == "warning"]

    return json.dumps({
        "exit_code": res["exit_code"],
        "timed_out": res["timed_out"],
        "errors": len(errors),
        "warnings": len(warnings),
        "diagnostics": diagnostics[:200],
        "stderr": res["stderr"],
        "duration_s": res["duration_s"],
        "truncated": len(diagnostics) > 200,
    }, ensure_ascii=False)
```

**Step 2: Verify tool is registered**

Run: `python -c "import src.rust_mcp as m; print(list(m.mcp._tool_manager._tools.keys()))"`

Expected: `['cargo_env_info', 'cargo_build', 'cargo_test', 'cargo_clippy']`

**Step 3: Commit**

```bash
git add src/rust_mcp.py
git commit -m "feat(rust-tools): add cargo_clippy with JSON diagnostic parsing"
```

---

### Task 6: Update README.md

**Files:**
- Modify: `G:\git\mcp-python-tools\README.md`

**Step 1: Update the Servers table**

Change the description line to reflect 5 servers / 47 tools:

```
Four servers providing 43 tools  →  Five servers providing 47 tools
```

Add row to Servers table:

```markdown
| **rust-tools** | `src/rust_mcp.py` | 4 | Cargo build, test, clippy with structured diagnostics |
```

**Step 2: Add rust-tools to Prerequisites table**

```markdown
| **rust-tools** | [Rust toolchain](https://rustup.rs/) (cargo, rustc) |
```

**Step 3: Add `claude mcp add` command to Quick Start**

```bash
# rust-tools (user-level — works in every Rust project)
claude mcp add --scope user --transport stdio rust-tools \
  -- "/path/to/mcp-python-tools/.venv/Scripts/python" "/path/to/mcp-python-tools/src/rust_mcp.py"
```

**Step 4: Add rust-tools to Registration Strategy table**

```markdown
| rust-tools | User | Every Rust project benefits from these tools |
```

**Step 5: Add rust-tools permission to settings.json example**

```json
"mcp__rust-tools__*"
```

**Step 6: Add Tool Reference section**

```markdown
### rust-tools (4 tools)

| Tool | Description |
|------|-------------|
| `cargo_env_info` | Diagnostic info about Rust/Cargo installation |
| `cargo_build` | Build with structured error/warning diagnostics |
| `cargo_test` | Run tests and return results |
| `cargo_clippy` | Lint with structured clippy diagnostics |
```

**Step 7: Add rust-tools JSON configuration example**

```json
"rust-tools": {
  "command": "python",
  "args": ["G:/git/mcp-python-tools/src/rust_mcp.py"]
}
```

**Step 8: Commit**

```bash
git add README.md
git commit -m "docs: add rust-tools to README"
```

---

### Task 7: Update ClaudeCodeSetup HOWTO.md

**Files:**
- Modify: `G:\git\ClaudeCodeSetup\mcp-servers\HOWTO.md`

**Step 1: Add Rust Tools registration section**

Insert after the GitHub Tools section (line ~106), before the .NET Tools section:

```markdown
### Rust Tools (4 tools)

Cargo build, test, and clippy with structured diagnostics. Requires Rust toolchain (rustup).

**Tools:** `cargo_env_info`, `cargo_build`, `cargo_test`, `cargo_clippy`

```powershell
claude mcp add --scope user --transport stdio rust-tools `
  -- "G:\git\mcp-python-tools\.venv\Scripts\python.exe" "G:\git\mcp-python-tools\src\rust_mcp.py"
```

**Step 2: Add Rust to Prerequisites**

Add to the prerequisites list:

```markdown
- **Rust toolchain** (rustup, cargo, rustc) - for rust-tools
```

**Step 3: Commit**

```bash
cd G:\git\ClaudeCodeSetup
git add mcp-servers/HOWTO.md
git commit -m "docs: add rust-tools MCP server registration"
```

---

### Task 8: Register and verify

**Step 1: Register rust-tools in user config**

```bash
claude mcp add --scope user --transport stdio rust-tools \
  -- "G:\git\mcp-python-tools\.venv\Scripts\python.exe" "G:\git\mcp-python-tools\src\rust_mcp.py"
```

**Step 2: Verify server loads and all 4 tools are registered**

Run: `python -c "import src.rust_mcp as m; print(f'Server: {m.mcp.name}'); print(f'Tools: {list(m.mcp._tool_manager._tools.keys())}')"` from `G:\git\mcp-python-tools`

Expected:
```
Server: rust-tools
Tools: ['cargo_env_info', 'cargo_build', 'cargo_test', 'cargo_clippy']
```

**Step 3: Push both repos**

```bash
cd G:\git\mcp-python-tools && git push
cd G:\git\ClaudeCodeSetup && git push
```
