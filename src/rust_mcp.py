"""
Rust MCP Server Tools

Tools for interacting with Rust/Cargo projects:
- Environment info (cargo, rustc, rustup, tauri)
- Building with structured diagnostics
- Running tests
- Clippy linting with optional auto-fix
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
        # Windows: use taskkill to kill process tree
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
            )
        except Exception:
            pass
    else:
        # Unix: kill process group or single process
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


def run_cmd(cmd: list[str], cwd: str | None = None, timeout: int = 1800) -> dict:
    """
    Run a shell command and capture output.

    Args:
        cmd: Command and arguments as a list
        cwd: Working directory for the command
        timeout: Maximum execution time in seconds (default 30 minutes)

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
        - Avoids .cmd wrappers when possible (Windows)
        - Enforces timeout by killing process tree
        - Sets CARGO_TERM_COLOR=never for clean output

    Args:
        args: Cargo command arguments (without 'cargo' prefix)
        cwd: Working directory (project path)
        timeout_s: Timeout in seconds

    Returns:
        Dict with exit_code, stdout, stderr, timed_out, duration_s, cmd
    """
    env = os.environ.copy()
    env.update({
        "CARGO_TERM_COLOR": "never",
    })

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
        partial_out = ""
        try:
            partial_out, _ = p.communicate(timeout=5)
        except Exception:
            pass
        return {
            "exit_code": 124,
            "stdout": (partial_out or "")[-200_000:],
            "stderr": f"Timed out after {timeout_s}s: {' '.join(args)}",
            "timed_out": True,
            "duration_s": round(time.time() - start, 3),
            "cmd": cmd,
        }


def _parse_cargo_diagnostics(json_output: str) -> list[dict]:
    """
    Parse cargo --message-format=json output into structured diagnostics.

    Filters for lines with reason == "compiler-message" and extracts
    level, message, file, line, and column from the primary span.

    Args:
        json_output: Raw stdout from cargo with --message-format=json

    Returns:
        List of diagnostic dicts with level, message, file, line, column
    """
    diagnostics = []
    for line in json_output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("reason") != "compiler-message":
            continue
        msg = obj.get("message", {})
        level = msg.get("level", "")
        message = msg.get("message", "")

        # Extract file/line/column from primary span
        file_name = ""
        line_num = None
        column = None
        spans = msg.get("spans", [])
        for span in spans:
            if span.get("is_primary"):
                file_name = span.get("file_name", "")
                line_num = span.get("line_start")
                column = span.get("column_start")
                break

        diagnostics.append({
            "level": level,
            "message": message,
            "file": file_name,
            "line": line_num,
            "column": column,
        })
    return diagnostics


# -------------------------
# Rust/Cargo Tools
# -------------------------

@mcp.tool()
async def cargo_env_info() -> str:
    """
    Return diagnostic information about the Rust/Cargo installation.
    Runs cargo --version, rustc --version, rustup show active-toolchain,
    and cargo tauri --version. Useful for debugging Rust environment issues.

    Returns:
        JSON with platform, paths, versions, and tauri_cli presence
    """
    info = {
        "platform": os.name,
        "cargo": shutil.which("cargo"),
        "rustc": shutil.which("rustc"),
        "resolved_cargo_exe": _cargo_exe(),
    }
    if os.name == "nt":
        info["cargo_exe"] = shutil.which("cargo.exe")

    # cargo --version
    res = run_cmd([_cargo_exe(), "--version"], timeout=10)
    if res["exit_code"] == 0:
        info["cargo_version"] = res["stdout"].strip()

    # rustc --version
    rustc = shutil.which("rustc.exe" if os.name == "nt" else "rustc") or "rustc"
    res = run_cmd([rustc, "--version"], timeout=10)
    if res["exit_code"] == 0:
        info["rustc_version"] = res["stdout"].strip()

    # rustup show active-toolchain
    rustup = shutil.which("rustup.exe" if os.name == "nt" else "rustup") or "rustup"
    res = run_cmd([rustup, "show", "active-toolchain"], timeout=10)
    if res["exit_code"] == 0:
        info["active_toolchain"] = res["stdout"].strip()

    # cargo tauri --version
    res = run_cmd([_cargo_exe(), "tauri", "--version"], timeout=10)
    if res["exit_code"] == 0:
        info["tauri_cli_version"] = res["stdout"].strip()
        info["tauri_cli"] = True
    else:
        info["tauri_cli"] = False

    return json.dumps(info, ensure_ascii=False)


@mcp.tool()
async def cargo_build(
    cwd: str,
    release: bool = False,
    target: str = "",
    features: str = "",
) -> str:
    """
    Run cargo build with structured diagnostic output.

    Args:
        cwd: Working directory (Rust project path)
        release: If True, build in release mode
        target: Optional target triple (e.g. x86_64-unknown-linux-gnu)
        features: Optional comma-separated feature list

    Returns:
        JSON with exit_code, timed_out, errors, warnings, diagnostics,
        stderr, duration_s, and truncated flag
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
    errors = sum(1 for d in diagnostics if d["level"] == "error")
    warnings = sum(1 for d in diagnostics if d["level"] == "warning")
    truncated = len(diagnostics) > 200

    return json.dumps({
        "exit_code": res["exit_code"],
        "timed_out": res["timed_out"],
        "errors": errors,
        "warnings": warnings,
        "diagnostics": diagnostics[:200],
        "stderr": res["stderr"],
        "duration_s": res["duration_s"],
        "truncated": truncated,
    }, ensure_ascii=False)


@mcp.tool()
async def cargo_test(
    cwd: str,
    test_name: str = "",
    release: bool = False,
) -> str:
    """
    Run cargo test, optionally filtering by test name.

    Args:
        cwd: Working directory (Rust project path)
        test_name: Optional test name filter
        release: If True, test in release mode

    Returns:
        JSON with exit_code, timed_out, stdout, stderr, and duration_s
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


@mcp.tool()
async def cargo_clippy(
    cwd: str,
    fix: bool = False,
) -> str:
    """
    Run cargo clippy for linting with structured diagnostic output.

    Args:
        cwd: Working directory (Rust project path)
        fix: If True, auto-fix warnings (--fix --allow-dirty)

    Returns:
        JSON with exit_code, timed_out, errors, warnings, diagnostics,
        stderr, duration_s, and truncated flag
    """
    args = ["clippy"]
    if fix:
        # --fix is incompatible with --message-format=json; return raw output
        args.extend(["--fix", "--allow-dirty"])
    else:
        args.append("--message-format=json")
    args.extend(["--", "-D", "warnings"])

    res = run_cargo(args, cwd=cwd, timeout_s=300)

    if fix:
        return json.dumps({
            "exit_code": res["exit_code"],
            "timed_out": res["timed_out"],
            "stdout": res["stdout"],
            "stderr": res["stderr"],
            "duration_s": res["duration_s"],
        }, ensure_ascii=False)

    diagnostics = _parse_cargo_diagnostics(res["stdout"])
    errors = sum(1 for d in diagnostics if d["level"] == "error")
    warnings = sum(1 for d in diagnostics if d["level"] == "warning")
    truncated = len(diagnostics) > 200

    return json.dumps({
        "exit_code": res["exit_code"],
        "timed_out": res["timed_out"],
        "errors": errors,
        "warnings": warnings,
        "diagnostics": diagnostics[:200],
        "stderr": res["stderr"],
        "duration_s": res["duration_s"],
        "truncated": truncated,
    }, ensure_ascii=False)


# -------------------------
# Entry point
# -------------------------

if __name__ == "__main__":
    mcp.run(transport="stdio")
