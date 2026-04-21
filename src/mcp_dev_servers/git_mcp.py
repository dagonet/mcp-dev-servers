"""
Git MCP Server Tools

Tools for interacting with Git repositories:
- Status, staging, committing
- Diff and log viewing
- Branch operations
"""

import shutil
import os
import json
import time
import subprocess
import signal
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("git-tools")

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


def _git_exe() -> str:
    """
    Find the git executable path, preferring .exe over .cmd on Windows.

    Returns:
        Path to git executable
    """
    if os.name == "nt":
        # Prefer git.exe, not git.cmd (cmd wrappers can break timeout/kill behavior)
        p = shutil.which("git.exe")
        if p:
            return p
    p = shutil.which("git")
    if p:
        return p
    return "git.exe" if os.name == "nt" else "git"


def run_git(args: list[str], cwd: str, timeout_s: int = 20) -> dict:
    """
    Run git safely in stdio MCP context (cross-platform).

    Features:
        - Does not inherit MCP stdin
        - Avoids git.cmd wrappers when possible (Windows)
        - Enforces timeout by killing process tree

    Args:
        args: Git command arguments (without 'git' prefix)
        cwd: Working directory (repository path)
        timeout_s: Timeout in seconds

    Returns:
        Dict with exit_code, stdout, stderr, timed_out, duration_s, cmd
    """
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


# -------------------------
# Git Tools
# -------------------------

@mcp.tool()
async def git_env_info() -> str:
    """
    Return diagnostic information about the git installation.
    Useful for debugging git-related issues.

    Returns:
        JSON with platform, git paths, and version info
    """
    info = {
        "platform": os.name,
        "git": shutil.which("git"),
        "resolved_git_exe": _git_exe(),
    }
    if os.name == "nt":
        info["git_exe"] = shutil.which("git.exe")
        where_exe = shutil.which("where.exe")
        if where_exe:
            info["where_git"] = run_cmd(["where.exe", "git"])["stdout"]
    else:
        which_result = run_cmd(["which", "git"])
        info["which_git"] = which_result["stdout"].strip() if which_result["exit_code"] == 0 else None

    # Get git version
    version_result = run_git(["--version"], cwd=".", timeout_s=5)
    if version_result["exit_code"] == 0:
        info["version"] = version_result["stdout"].strip()

    return json.dumps(info, ensure_ascii=False)


@mcp.tool()
async def git_status(repo_path: str, include_untracked: bool = True, timeout_s: int = 20) -> str:
    """
    Fast porcelain git status.

    Args:
        repo_path: Path to the git repository
        include_untracked: Include untracked files (default True)
        timeout_s: Timeout in seconds

    Returns:
        JSON with file statuses
    """
    args = ["status", "--porcelain=v1", "-z", "--ignore-submodules=all"]
    if not include_untracked:
        args.append("-uno")

    res = run_git(args, cwd=repo_path, timeout_s=timeout_s)

    files = []
    if res["stdout"]:
        for entry in res["stdout"].split("\0"):
            if not entry:
                continue
            status = entry[:2]
            path = entry[3:] if len(entry) > 3 else ""
            files.append({"status": status, "path": path})

    return json.dumps({
        "exit_code": res["exit_code"],
        "timed_out": res["timed_out"],
        "files": files,
        "stderr": (res["stderr"] or "").strip(),
        "duration_s": res.get("duration_s"),
    }, ensure_ascii=False)


@mcp.tool()
async def git_add(repo_path: str, paths: list[str]) -> str:
    """
    Stage specific files for commit.

    Args:
        repo_path: Path to the git repository
        paths: List of file paths to stage

    Returns:
        JSON with exit_code and any output
    """
    if not paths:
        return json.dumps({"error": "No paths provided"}, ensure_ascii=False)

    res = run_git(["add", "--"] + paths, cwd=repo_path, timeout_s=60)

    return json.dumps({
        "exit_code": res["exit_code"],
        "stdout": res["stdout"],
        "stderr": res["stderr"],
    }, ensure_ascii=False)


@mcp.tool()
async def git_rm(repo_path: str, paths: list[str], cached: bool = False) -> str:
    """
    Remove files from git tracking.

    Args:
        repo_path: Path to the git repository
        paths: List of file paths to remove
        cached: If True, only remove from index (keep working tree files)

    Returns:
        JSON with exit_code and any output
    """
    if not paths:
        return json.dumps({"error": "No paths provided"}, ensure_ascii=False)

    cmd = ["rm"]
    if cached:
        cmd.append("--cached")

    res = run_git(cmd + ["--"] + paths, cwd=repo_path, timeout_s=60)

    return json.dumps({
        "exit_code": res["exit_code"],
        "stdout": res["stdout"],
        "stderr": res["stderr"],
    }, ensure_ascii=False)


@mcp.tool()
async def git_commit(repo_path: str, message: str) -> str:
    """
    Create a git commit with the provided message.

    Args:
        repo_path: Path to the git repository
        message: Commit message

    Returns:
        JSON with exit_code and commit output
    """
    if not message or not message.strip():
        return json.dumps({"error": "Commit message must not be empty"}, ensure_ascii=False)

    res = run_git(["commit", "-m", message], cwd=repo_path, timeout_s=90)

    return json.dumps({
        "exit_code": res["exit_code"],
        "stdout": res["stdout"],
        "stderr": res["stderr"],
    }, ensure_ascii=False)


@mcp.tool()
async def git_diff_summary(repo_path: str, staged: bool = False) -> str:
    """
    Return a compact diffstat summary.

    Args:
        repo_path: Path to the git repository
        staged: If True, show staged changes; otherwise show unstaged

    Returns:
        JSON with diff summary
    """
    cmd = ["diff", "--stat"]
    if staged:
        cmd.insert(1, "--cached")

    res = run_git(cmd, cwd=repo_path, timeout_s=20)

    return json.dumps({
        "exit_code": res["exit_code"],
        "summary": res["stdout"],
        "stderr": res["stderr"],
    }, ensure_ascii=False)


@mcp.tool()
async def git_diff(repo_path: str, staged: bool = False, file_path: str | None = None) -> str:
    """
    Get the full diff output.

    Args:
        repo_path: Path to the git repository
        staged: If True, show staged changes; otherwise show unstaged
        file_path: Optional specific file to diff

    Returns:
        JSON with full diff output
    """
    cmd = ["diff"]
    if staged:
        cmd.append("--cached")
    if file_path:
        cmd.extend(["--", file_path])

    res = run_git(cmd, cwd=repo_path, timeout_s=30)

    return json.dumps({
        "exit_code": res["exit_code"],
        "diff": res["stdout"][:100_000],  # Limit output size
        "stderr": res["stderr"],
        "truncated": len(res["stdout"]) > 100_000,
    }, ensure_ascii=False)


@mcp.tool()
async def git_log(repo_path: str, limit: int = 10, oneline: bool = True) -> str:
    """
    Return recent commit history.

    Args:
        repo_path: Path to the git repository
        limit: Maximum number of commits to return
        oneline: If True, use compact one-line format

    Returns:
        JSON with commit log
    """
    cmd = ["log", f"-{limit}"]
    if oneline:
        cmd.append("--oneline")
    else:
        cmd.extend(["--format=%H|%an|%ae|%s|%ai"])

    res = run_git(cmd, cwd=repo_path, timeout_s=20)

    return json.dumps({
        "exit_code": res["exit_code"],
        "log": res["stdout"],
        "stderr": res["stderr"],
    }, ensure_ascii=False)


@mcp.tool()
async def git_branch_list(repo_path: str, all_branches: bool = False) -> str:
    """
    List git branches.

    Args:
        repo_path: Path to the git repository
        all_branches: If True, include remote branches

    Returns:
        JSON with branch list and current branch
    """
    cmd = ["branch", "--format=%(refname:short)|%(upstream:short)|%(HEAD)"]
    if all_branches:
        cmd.append("-a")

    res = run_git(cmd, cwd=repo_path, timeout_s=10)

    branches = []
    current = None
    for line in res["stdout"].strip().splitlines():
        if not line:
            continue
        parts = line.split("|")
        name = parts[0] if parts else ""
        upstream = parts[1] if len(parts) > 1 else ""
        is_current = parts[2] == "*" if len(parts) > 2 else False

        if name:
            branch = {"name": name, "upstream": upstream or None, "current": is_current}
            branches.append(branch)
            if is_current:
                current = name

    return json.dumps({
        "exit_code": res["exit_code"],
        "current": current,
        "branches": branches,
        "stderr": res["stderr"],
    }, ensure_ascii=False)


@mcp.tool()
async def git_checkout(repo_path: str, ref: str, create: bool = False) -> str:
    """
    Checkout a branch, tag, or commit.

    Args:
        repo_path: Path to the git repository
        ref: Branch name, tag, or commit hash
        create: If True, create a new branch (-b flag)

    Returns:
        JSON with exit_code and output
    """
    cmd = ["checkout"]
    if create:
        cmd.append("-b")
    cmd.append(ref)

    res = run_git(cmd, cwd=repo_path, timeout_s=30)

    return json.dumps({
        "exit_code": res["exit_code"],
        "stdout": res["stdout"],
        "stderr": res["stderr"],
    }, ensure_ascii=False)


@mcp.tool()
async def git_branch_delete(repo_path: str, name: str, force: bool = False) -> str:
    """
    Delete a local git branch. Refuses to delete the currently checked-out branch.

    Args:
        repo_path: Path to the git repository
        name: Branch name to delete
        force: If True, use -D (force delete even if unmerged). Default uses -d
               which requires the branch to be merged.

    Returns:
        JSON with exit_code and output
    """
    if not name:
        return json.dumps({"error": "branch name required"}, ensure_ascii=False)

    current = run_git(["branch", "--show-current"], cwd=repo_path, timeout_s=10)
    if current["stdout"].strip() == name:
        return json.dumps(
            {"error": f"refusing to delete current branch '{name}'"},
            ensure_ascii=False,
        )

    flag = "-D" if force else "-d"
    res = run_git(["branch", flag, name], cwd=repo_path, timeout_s=20)

    return json.dumps({
        "exit_code": res["exit_code"],
        "stdout": res["stdout"],
        "stderr": res["stderr"],
    }, ensure_ascii=False)


@mcp.tool()
async def git_fetch(repo_path: str, remote: str = "origin",
                    branch: str | None = None, prune: bool = False) -> str:
    """
    Fetch from remote without merging (unlike git_pull).

    Args:
        repo_path: Path to the git repository
        remote: Remote name (default: origin)
        branch: Specific branch to fetch (default: all configured refspecs)
        prune: If True, append --prune to remove stale remote-tracking refs

    Returns:
        JSON with exit_code and output
    """
    cmd = ["fetch"]
    if prune:
        cmd.append("--prune")
    cmd.append(remote)
    if branch:
        cmd.append(branch)

    res = run_git(cmd, cwd=repo_path, timeout_s=120)

    return json.dumps({
        "exit_code": res["exit_code"],
        "stdout": res["stdout"],
        "stderr": res["stderr"],
    }, ensure_ascii=False)


@mcp.tool()
async def git_pull(repo_path: str, remote: str = "origin", branch: str | None = None) -> str:
    """
    Pull changes from remote.

    Args:
        repo_path: Path to the git repository
        remote: Remote name (default: origin)
        branch: Branch to pull (default: current branch)

    Returns:
        JSON with exit_code and output
    """
    cmd = ["pull", remote]
    if branch:
        cmd.append(branch)

    res = run_git(cmd, cwd=repo_path, timeout_s=120)

    return json.dumps({
        "exit_code": res["exit_code"],
        "stdout": res["stdout"],
        "stderr": res["stderr"],
    }, ensure_ascii=False)


@mcp.tool()
async def git_push(repo_path: str, remote: str = "origin", branch: str | None = None,
                   set_upstream: bool = False, force: bool = False, delete: bool = False) -> str:
    """
    Push changes to remote.

    Args:
        repo_path: Path to the git repository
        remote: Remote name (default: origin)
        branch: Branch to push (default: current branch)
        set_upstream: If True, set upstream tracking (-u flag)
        force: If True, append --force-with-lease (safer than --force; rejects push
               if upstream changed since last fetch)
        delete: If True, append --delete to remove the remote branch. Requires branch
                to be set. Ignores set_upstream (no upstream to set on a deleted ref).

    Returns:
        JSON with exit_code and output
    """
    if delete and not branch:
        return json.dumps({"error": "delete=True requires branch to be set"}, ensure_ascii=False)

    cmd = ["push"]
    if set_upstream:
        cmd.append("-u")
    if force:
        cmd.append("--force-with-lease")
    if delete:
        cmd.append("--delete")
    cmd.append(remote)
    if branch:
        cmd.append(branch)

    res = run_git(cmd, cwd=repo_path, timeout_s=120)

    return json.dumps({
        "exit_code": res["exit_code"],
        "stdout": res["stdout"],
        "stderr": res["stderr"],
    }, ensure_ascii=False)


@mcp.tool()
async def git_reset(repo_path: str, ref: str, mode: str = "mixed") -> str:
    """
    Reset current HEAD to the specified ref.

    Args:
        repo_path: Path to the git repository
        ref: Target ref (commit hash, branch, tag, HEAD~N, etc.)
        mode: One of "soft" (keep index + worktree), "mixed" (keep worktree,
              reset index; default), or "hard" (discard all uncommitted changes)

    Returns:
        JSON with exit_code and output. For mode="hard", also includes a
        "warning" field to flag the destructive operation.
    """
    valid_modes = {"soft", "mixed", "hard"}
    if mode not in valid_modes:
        return json.dumps({"error": "mode must be soft|mixed|hard"}, ensure_ascii=False)
    if not ref:
        return json.dumps({"error": "ref required"}, ensure_ascii=False)

    res = run_git(["reset", f"--{mode}", ref], cwd=repo_path, timeout_s=30)

    payload = {
        "exit_code": res["exit_code"],
        "stdout": res["stdout"],
        "stderr": res["stderr"],
    }
    if mode == "hard":
        payload["warning"] = "hard reset discards uncommitted changes"

    return json.dumps(payload, ensure_ascii=False)


@mcp.tool()
async def git_stash(repo_path: str, action: str = "push", message: str | None = None) -> str:
    """
    Stash or restore working directory changes.

    Args:
        repo_path: Path to the git repository
        action: One of "push", "pop", "list", "drop", "clear"
        message: Optional message for stash push

    Returns:
        JSON with exit_code and output
    """
    valid_actions = {"push", "pop", "list", "drop", "clear", "show"}
    if action not in valid_actions:
        return json.dumps({"error": f"Invalid action. Must be one of: {valid_actions}"}, ensure_ascii=False)

    cmd = ["stash", action]
    if action == "push" and message:
        cmd.extend(["-m", message])

    res = run_git(cmd, cwd=repo_path, timeout_s=30)

    return json.dumps({
        "exit_code": res["exit_code"],
        "stdout": res["stdout"],
        "stderr": res["stderr"],
    }, ensure_ascii=False)


@mcp.tool()
async def git_remote_list(repo_path: str) -> str:
    """
    List configured git remotes.

    Args:
        repo_path: Path to the git repository

    Returns:
        JSON with remote names and URLs
    """
    res = run_git(["remote", "-v"], cwd=repo_path, timeout_s=10)

    remotes = {}
    for line in res["stdout"].strip().splitlines():
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 2:
            name = parts[0]
            url = parts[1]
            remote_type = parts[2].strip("()") if len(parts) > 2 else "fetch"
            if name not in remotes:
                remotes[name] = {}
            remotes[name][remote_type] = url

    return json.dumps({
        "exit_code": res["exit_code"],
        "remotes": remotes,
        "stderr": res["stderr"],
    }, ensure_ascii=False)


@mcp.tool()
async def git_tag_list(repo_path: str, limit: int = 20) -> str:
    """
    List git tags.

    Args:
        repo_path: Path to the git repository
        limit: Maximum number of tags to return

    Returns:
        JSON with tag list
    """
    res = run_git(["tag", "-l", "--sort=-creatordate"], cwd=repo_path, timeout_s=10)

    tags = [t for t in res["stdout"].strip().splitlines() if t][:limit]

    return json.dumps({
        "exit_code": res["exit_code"],
        "tags": tags,
        "count": len(tags),
        "stderr": res["stderr"],
    }, ensure_ascii=False)


@mcp.tool()
async def git_show(repo_path: str, ref: str = "HEAD") -> str:
    """
    Show information about a commit.

    Args:
        repo_path: Path to the git repository
        ref: Commit reference (default: HEAD)

    Returns:
        JSON with commit details
    """
    res = run_git(["show", "--stat", "--format=%H%n%an%n%ae%n%ai%n%s%n%b", ref], cwd=repo_path, timeout_s=20)

    commit = {}
    if res["exit_code"] == 0 and res["stdout"]:
        lines = res["stdout"].split("\n", 5)
        if len(lines) >= 5:
            commit = {
                "hash": lines[0],
                "author": lines[1],
                "email": lines[2],
                "date": lines[3],
                "subject": lines[4],
                "body": lines[5].split("\n\n")[0] if len(lines) > 5 else "",
                "stat": "\n".join(lines[5].split("\n\n")[1:]) if len(lines) > 5 and "\n\n" in lines[5] else "",
            }

    return json.dumps({
        "exit_code": res["exit_code"],
        "commit": commit,
        "stderr": res["stderr"],
    }, ensure_ascii=False)


# -------------------------
# Entry point
# -------------------------

def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
