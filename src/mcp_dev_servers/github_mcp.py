"""
GitHub MCP Server Tools (Slim)

Provides tools NOT available in the official GitHub MCP server:
- gh_repo_from_origin: Get OWNER/REPO from local git remote
- gh_workflow_list: List GitHub Actions workflow runs

For all other GitHub operations (issues, PRs, releases, etc.),
use the official GitHub MCP server (mcp__github__).
"""

import shutil
import os
import json
import time
import subprocess
import signal
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("github-tools")

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


def _git_exe() -> str:
    """Find the git executable path, preferring .exe over .cmd on Windows."""
    if os.name == "nt":
        p = shutil.which("git.exe")
        if p:
            return p
    p = shutil.which("git")
    if p:
        return p
    return "git.exe" if os.name == "nt" else "git"


def run_git(args: list[str], cwd: str, timeout_s: int = 20) -> dict:
    """Run git safely in stdio MCP context (cross-platform)."""
    env = os.environ.copy()
    env.update({
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_PAGER": "cat",
        "PAGER": "cat",
        "LC_ALL": "C",
    })

    exe = _git_exe()
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
            "stdout": out or "",
            "stderr": err or "",
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


def run_gh(args: list[str], cwd: str | None = None, timeout_s: int = 30) -> dict:
    """Robust, non-interactive GitHub CLI runner for MCP usage."""
    gh_exe = (
        os.environ.get("GH_EXE")
        or shutil.which("gh.exe")
        or shutil.which("gh")
        or ("gh.exe" if os.name == "nt" else "gh")
    )

    env = os.environ.copy()
    env.update({
        "GH_PAGER": "cat",
        "PAGER": "cat",
        "GIT_TERMINAL_PROMPT": "0",
        "GH_PROMPT_DISABLED": "1",
    })

    cmd = [gh_exe] + args
    start = time.time()

    try:
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

        stdout, stderr = p.communicate(timeout=timeout_s)

        return {
            "exit_code": p.returncode,
            "stdout": stdout or "",
            "stderr": stderr or "",
            "timed_out": False,
            "duration_s": round(time.time() - start, 3),
            "cmd": " ".join(cmd),
        }

    except subprocess.TimeoutExpired:
        _kill_process_tree(p.pid)
        return {
            "exit_code": 124,
            "stdout": "",
            "stderr": f"gh command timed out after {timeout_s}s",
            "timed_out": True,
            "duration_s": round(time.time() - start, 3),
            "cmd": " ".join(cmd),
        }


# -------------------------
# GitHub Tools (Unique - not in official MCP)
# -------------------------

@mcp.tool()
async def gh_repo_from_origin(repo_path: str, timeout_s: int = 10) -> str:
    """
    Returns OWNER/REPO derived from 'git remote get-url origin'.
    Supports SSH and HTTPS remotes.

    Args:
        repo_path: Path to the git repository
        timeout_s: Timeout in seconds

    Returns:
        JSON with repo in OWNER/REPO format
    """
    res = run_git(["remote", "get-url", "origin"], cwd=repo_path, timeout_s=timeout_s)
    if res.get("exit_code") != 0 or not res.get("stdout"):
        return json.dumps({"exit_code": 1, "error": res.get("stderr", "").strip()}, ensure_ascii=False)

    url = res["stdout"].strip()

    # Examples:
    # git@github.com:owner/repo.git
    # https://github.com/owner/repo.git
    owner_repo = None

    if url.startswith("git@"):
        try:
            owner_repo = url.split(":", 1)[1]
        except Exception:
            owner_repo = None
    elif "github.com/" in url:
        owner_repo = url.split("github.com/", 1)[1]

    if owner_repo and owner_repo.endswith(".git"):
        owner_repo = owner_repo[:-4]

    if not owner_repo or "/" not in owner_repo:
        return json.dumps({"exit_code": 2, "error": f"Could not parse owner/repo from origin: {url}"}, ensure_ascii=False)

    return json.dumps({"exit_code": 0, "repo": owner_repo}, ensure_ascii=False)


@mcp.tool()
async def gh_workflow_list(repo: str, limit: int = 20) -> str:
    """
    List recent workflow runs in a GitHub repository.

    Args:
        repo: Repository in OWNER/REPO format
        limit: Maximum number of runs to return

    Returns:
        JSON with list of workflow runs
    """
    if "/" not in repo:
        return json.dumps({"exit_code": 2, "error": f"Invalid repo '{repo}'. Expected 'OWNER/REPO'."}, ensure_ascii=False)

    res = run_gh(
        ["run", "list", "--repo", repo, "--limit", str(limit),
         "--json", "databaseId,workflowName,status,conclusion,event,headBranch,createdAt,url"],
        timeout_s=30,
    )
    if res["exit_code"] != 0:
        return json.dumps({"exit_code": res["exit_code"], "error": res["stderr"]}, ensure_ascii=False)

    try:
        runs = json.loads(res["stdout"])
        return json.dumps({"exit_code": 0, "workflow_runs": runs}, ensure_ascii=False)
    except json.JSONDecodeError:
        return json.dumps({"exit_code": 0, "raw": res["stdout"]}, ensure_ascii=False)


# -------------------------
# Entry point
# -------------------------

def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
