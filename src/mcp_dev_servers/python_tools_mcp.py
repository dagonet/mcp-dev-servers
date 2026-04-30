"""
Python Development MCP Server Tools

Tools for Python project workflows:
- Smoke install (venv creation + wheel install + command run)
- Wheel and sdist inspection
- pytest, ruff, uv build, and coverage wrappers
"""

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import tarfile
import tempfile
import time
import zipfile
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("python-tools")

_SUBPROCESS_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


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


def run_tool(
    cmd: list[str],
    cwd: str | None = None,
    timeout_s: int = 120,
) -> dict:
    """Run a tool subprocess with timeout and capture."""
    start = time.time()
    try:
        p = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=False,
            creationflags=_SUBPROCESS_FLAGS,
        )
        out, err = p.communicate(timeout=timeout_s)
        return {
            "exit_code": p.returncode,
            "stdout": out or "",
            "stderr": err or "",
            "timed_out": False,
            "duration_s": round(time.time() - start, 3),
        }
    except subprocess.TimeoutExpired:
        _kill_process_tree(p.pid)
        return {
            "exit_code": 124,
            "stdout": "",
            "stderr": f"Timed out after {timeout_s}s",
            "timed_out": True,
            "duration_s": round(time.time() - start, 3),
        }


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def wheel_inspect(wheel_path: str) -> str:
    """Read METADATA, version, and entry points from a wheel without installing it."""
    if not os.path.exists(wheel_path):
        return json.dumps({"error": f"wheel not found: {wheel_path}"}, ensure_ascii=False)
    try:
        with zipfile.ZipFile(wheel_path, "r") as zf:
            file_list = zf.namelist()
            dist_info = None
            for name in file_list:
                if name.endswith(".dist-info/") or name.endswith(".dist-info/METADATA"):
                    dist_info = name.rsplit("/", 1)[0] if "/" in name else name
                    break
            if not dist_info:
                return json.dumps({"error": "no .dist-info directory found"}, ensure_ascii=False)
            metadata_path = f"{dist_info}/METADATA"
            if metadata_path not in file_list:
                return json.dumps({"error": "METADATA not found"}, ensure_ascii=False)
            metadata = zf.read(metadata_path).decode("utf-8")
            pkg_name = ""
            version = ""
            requires_python = None
            for line in metadata.splitlines():
                if line.startswith("Name:"):
                    pkg_name = line.split(":", 1)[1].strip()
                elif line.startswith("Version:"):
                    version = line.split(":", 1)[1].strip()
                elif line.startswith("Requires-Python:"):
                    requires_python = line.split(":", 1)[1].strip()
            entry_points = {}
            ep_path = f"{dist_info}/entry_points.txt"
            if ep_path in file_list:
                ep_content = zf.read(ep_path).decode("utf-8")
                current_section = None
                for line in ep_content.splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if line.startswith("[") and line.endswith("]"):
                        current_section = line[1:-1]
                        entry_points[current_section] = {}
                    elif "=" in line and current_section:
                        k, v = line.split("=", 1)
                        entry_points[current_section][k.strip()] = v.strip()
            return json.dumps(
                {
                    "name": pkg_name,
                    "version": version,
                    "requires_python": requires_python,
                    "entry_points": entry_points,
                    "files": file_list,
                },
                ensure_ascii=False,
            )
    except zipfile.BadZipFile as e:
        return json.dumps({"error": f"invalid zip file: {e}"}, ensure_ascii=False)


@mcp.tool()
async def sdist_inspect(sdist_path: str) -> str:
    """Read PKG-INFO and file manifest from a source distribution."""
    if not os.path.exists(sdist_path):
        return json.dumps({"error": f"sdist not found: {sdist_path}"}, ensure_ascii=False)
    try:
        with tarfile.open(sdist_path, "r:gz") as tf:
            members = tf.getnames()
            pkg_info = None
            for name in members:
                if name.endswith("/PKG-INFO") or name == "PKG-INFO":
                    pkg_info = name
                    break
            if not pkg_info:
                return json.dumps({"error": "PKG-INFO not found"}, ensure_ascii=False)
            f = tf.extractfile(pkg_info)
            if not f:
                return json.dumps({"error": f"could not read {pkg_info}"}, ensure_ascii=False)
            content = f.read().decode("utf-8")
            pkg_name = ""
            version = ""
            for line in content.splitlines():
                if line.startswith("Name:"):
                    pkg_name = line.split(":", 1)[1].strip()
                elif line.startswith("Version:"):
                    version = line.split(":", 1)[1].strip()
            return json.dumps(
                {"name": pkg_name, "version": version, "files": members},
                ensure_ascii=False,
            )
    except tarfile.TarError as e:
        return json.dumps({"error": f"invalid tar file: {e}"}, ensure_ascii=False)


@mcp.tool()
async def python_smoke_install(
    wheel_path: str,
    commands: list[str],
    python_version: str | None = None,
    cleanup: bool = True,
) -> str:
    """Create throwaway venv, install wheel, run commands. Cross-platform."""
    if not os.path.exists(wheel_path):
        return json.dumps({"error": f"wheel not found: {wheel_path}"}, ensure_ascii=False)
    if not commands:
        return json.dumps({"error": "at least one command required"}, ensure_ascii=False)
    venv_dir = tempfile.mkdtemp(prefix="smoke_venv_")
    python_exe = None
    scripts_dir = None
    results = {}
    cleaned_up = False
    try:
        import venv

        builder = venv.EnvBuilder(with_pip=True)
        builder.create(venv_dir)
        if os.name == "nt" and os.path.exists(os.path.join(venv_dir, "Scripts")):
            scripts_dir = os.path.join(venv_dir, "Scripts")
            python_exe = os.path.join(scripts_dir, "python.exe")
        else:
            scripts_dir = os.path.join(venv_dir, "bin")
            python_exe = os.path.join(scripts_dir, "python3")
            if not os.path.exists(python_exe):
                python_exe = os.path.join(scripts_dir, "python")
        if not os.path.exists(python_exe):
            return json.dumps({"error": "python not found in venv"}, ensure_ascii=False)
        install_res = run_tool([python_exe, "-m", "pip", "install", wheel_path], timeout_s=120)
        if install_res["exit_code"] != 0:
            results["_install"] = {
                "stdout": install_res["stdout"],
                "exit_code": install_res["exit_code"],
                "stderr": install_res["stderr"],
            }
            return json.dumps(
                {
                    "error": "pip install failed",
                    "results": results,
                    "python_path": python_exe,
                    "scripts_dir": scripts_dir,
                },
                ensure_ascii=False,
            )
        for cmd_str in commands:
            cmd_path = os.path.join(scripts_dir, cmd_str.split()[0])
            resolved = [cmd_path] + cmd_str.split()[1:] if os.path.exists(cmd_path) else cmd_str.split()
            cmd_res = run_tool(resolved, timeout_s=30)
            results[cmd_str] = {
                "stdout": cmd_res["stdout"],
                "exit_code": cmd_res["exit_code"],
            }
    finally:
        if cleanup:
            try:
                shutil.rmtree(venv_dir)
                cleaned_up = True
            except OSError:
                pass
    return json.dumps(
        {
            "results": results,
            "python_path": python_exe,
            "scripts_dir": scripts_dir,
            "cleaned_up": cleaned_up,
        },
        ensure_ascii=False,
    )


@mcp.tool()
async def uv_build(
    repo_path: str,
    clean: bool = True,
    targets: str = "wheel,sdist",
) -> str:
    """Build Python distributions with uv. Clean+build+collect in one call."""
    dist_dir = os.path.join(repo_path, "dist")
    if clean and os.path.exists(dist_dir):
        try:
            shutil.rmtree(dist_dir)
        except OSError as e:
            return json.dumps({"error": f"failed to clean dist/: {e}"}, ensure_ascii=False)
    res = run_tool(["uv", "build"], cwd=repo_path, timeout_s=180)
    if res["exit_code"] != 0:
        return json.dumps(
            {"error": "uv build failed", "stderr": res["stderr"], "stdout": res["stdout"]},
            ensure_ascii=False,
        )
    artifacts = []
    if os.path.isdir(dist_dir):
        for fname in sorted(os.listdir(dist_dir)):
            fpath = os.path.join(dist_dir, fname)
            if not os.path.isfile(fpath):
                continue
            size_bytes = os.path.getsize(fpath)
            h = hashlib.sha256()
            with open(fpath, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
            artifacts.append(
                {
                    "path": fpath,
                    "name": fname,
                    "size_bytes": size_bytes,
                    "sha256": h.hexdigest(),
                }
            )
    return json.dumps({"artifacts": artifacts}, ensure_ascii=False)


@mcp.tool()
async def pytest_run(
    repo_path: str,
    paths: list[str] | None = None,
    markers: str | None = None,
    keyword: str | None = None,
    extra_args: list[str] | None = None,
) -> str:
    """Run pytest with structured output (counts + failure details)."""
    cmd = ["pytest", "--tb=short"]
    if markers:
        cmd.extend(["-m", markers])
    if keyword:
        cmd.extend(["-k", keyword])
    if extra_args:
        cmd.extend(extra_args)
    cmd.extend(paths if paths else ["tests/"])
    res = run_tool(cmd, cwd=repo_path, timeout_s=600)
    stdout = res["stdout"]
    passed = failed = skipped = xfailed = xpassed = deselected = 0
    failures = []
    for line in stdout.splitlines():
        if " passed" in line or " failed" in line:
            m = re.findall(r"(\d+) (\w+)", line)
            for count_str, label in m:
                count = int(count_str)
                if label == "passed":
                    passed = count
                elif label == "failed":
                    failed = count
                elif label == "skipped":
                    skipped = count
                elif label == "xfailed":
                    xfailed = count
                elif label == "xpassed":
                    xpassed = count
                elif label == "deselected":
                    deselected = count
    in_failures = False
    current_failure = None
    for line in stdout.splitlines():
        if line.startswith("FAILURES"):
            in_failures = True
            continue
        if in_failures and line.startswith("_") and "_" in line:
            if current_failure:
                failures.append(current_failure)
            current_failure = {"name": line.strip(), "message": ""}
        elif in_failures and current_failure:
            if line.strip():
                current_failure["message"] += line + "\n"
    if current_failure:
        failures.append(current_failure)
    return json.dumps(
        {
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "xfailed": xfailed,
            "xpassed": xpassed,
            "deselected": deselected,
            "exit_code": res["exit_code"],
            "duration_s": res["duration_s"],
            "failures": failures,
        },
        ensure_ascii=False,
    )


@mcp.tool()
async def ruff(
    repo_path: str,
    mode: str = "check",
    paths: list[str] | None = None,
    fix: bool = False,
) -> str:
    """Run ruff linter or formatter. Mode: 'check' or 'format'."""
    if mode not in ("check", "format"):
        return json.dumps({"error": "mode must be 'check' or 'format'"}, ensure_ascii=False)
    if mode == "check":
        cmd = ["ruff", "check", "--output-format=json"]
        if fix:
            cmd.append("--fix")
    else:
        cmd = ["ruff", "format", "--check"]
    if paths:
        cmd.extend(paths)
    res = run_tool(cmd, cwd=repo_path, timeout_s=120)
    if mode == "check":
        violations = []
        if res["stdout"]:
            try:
                violations = json.loads(res["stdout"])
            except json.JSONDecodeError:
                pass
        return json.dumps(
            {
                "violations": [
                    {
                        "file": v.get("filename", ""),
                        "line": v.get("location", {}).get("row", 0),
                        "code": v.get("code", ""),
                        "message": v.get("message", ""),
                    }
                    for v in violations
                ],
                "exit_code": res["exit_code"],
            },
            ensure_ascii=False,
        )
    else:
        changed_files = []
        for line in res["stdout"].splitlines():
            if line.startswith("Would reformat:"):
                pass
            elif line.strip() and not line.startswith(" "):
                changed_files.append(line.strip())
        return json.dumps(
            {
                "changed_files": changed_files if res["exit_code"] != 0 else [],
                "would_change": changed_files if res["exit_code"] != 0 else [],
                "exit_code": res["exit_code"],
            },
            ensure_ascii=False,
        )


@mcp.tool()
async def coverage(
    repo_path: str,
    paths: list[str] | None = None,
    min_coverage: float | None = None,
) -> str:
    """Run tests under coverage and return typed coverage summary."""
    test_paths = paths or ["tests/"]
    run_res = run_tool(
        ["coverage", "run", "-m", "pytest"] + test_paths,
        cwd=repo_path,
        timeout_s=600,
    )
    if run_res["exit_code"] not in (0, 1):
        return json.dumps(
            {
                "error": f"coverage run failed with exit code {run_res['exit_code']}",
                "stderr": run_res["stderr"],
            },
            ensure_ascii=False,
        )
    json_res = run_tool(["coverage", "json", "-o", "-"], cwd=repo_path, timeout_s=30)
    if json_res["exit_code"] != 0:
        return json.dumps(
            {"error": "coverage json failed", "stderr": json_res["stderr"]},
            ensure_ascii=False,
        )
    try:
        data = json.loads(json_res["stdout"])
    except json.JSONDecodeError:
        return json.dumps({"error": "could not parse coverage json"}, ensure_ascii=False)
    totals = data.get("totals", {})
    total_pct = round(totals.get("percent_covered", 0), 1)
    files_data = data.get("files", {})
    per_file = {}
    missing_lines = {}
    for fpath, finfo in files_data.items():
        summary = finfo.get("summary", {})
        per_file[fpath] = round(summary.get("percent_covered", 0), 1)
        miss = finfo.get("missing_lines", [])
        if miss:
            missing_lines[fpath] = miss
    exit_code = 0
    if min_coverage is not None and total_pct < min_coverage:
        exit_code = 1
    return json.dumps(
        {
            "total_pct": total_pct,
            "per_file": per_file,
            "missing_lines": missing_lines,
            "exit_code": exit_code,
        },
        ensure_ascii=False,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
