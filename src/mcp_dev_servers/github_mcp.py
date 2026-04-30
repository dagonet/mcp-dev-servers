"""
GitHub MCP Server Tools

Provides tools NOT available in the official GitHub MCP server:
- Release management (create, edit, delete, upload/delete assets)
- Workflow dispatch and run management (dispatch, wait, rerun, cancel)
- Check runs, branch protection, PR labels/review/auto-merge
- gh_repo_from_origin and gh_workflow_list
"""

import shutil
import os
import json
import hashlib
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
# Release Management (D)
# -------------------------


@mcp.tool()
async def github_release_create(owner: str, repo: str, tag_name: str, target_commitish: str = "main", name: str | None = None, body: str = "", draft: bool = True, prerelease: bool = False) -> str:
    """Create a GitHub release. Defaults to draft mode. Does NOT accept assets."""
    if "/" in repo or "/" in owner: return json.dumps({"error": "provide owner and repo separately"}, ensure_ascii=False)
    cmd = ["release", "create", tag_name, "--repo", f"{owner}/{repo}", "--target", target_commitish, "--title", name or tag_name, "--notes", body or ""]
    if draft: cmd.append("--draft")
    if prerelease: cmd.append("--prerelease")
    res = run_gh(cmd, timeout_s=30)
    if res["exit_code"] != 0: return json.dumps({"error": res["stderr"]}, ensure_ascii=False)
    release_url = res["stdout"].strip()
    release_id = None
    if release_url:
        parts = release_url.rstrip("/").rsplit("/", 1)
        try: release_id = parts[-1] if parts[-1].isdigit() else None
        except (ValueError, IndexError): pass
    return json.dumps({"id": release_id, "tag_name": tag_name, "name": name or tag_name, "html_url": release_url, "draft": draft, "prerelease": prerelease}, ensure_ascii=False)


@mcp.tool()
async def github_release_edit(owner: str, repo: str, release_id: str, tag_name: str | None = None, target_commitish: str | None = None, name: str | None = None, body: str | None = None, draft: bool | None = None, prerelease: bool | None = None) -> str:
    """Edit an existing GitHub release. Pass draft=false to publish."""
    cmd = ["release", "edit", release_id, "--repo", f"{owner}/{repo}"]
    if tag_name: cmd.extend(["--tag", tag_name])
    if target_commitish: cmd.extend(["--target", target_commitish])
    if name is not None: cmd.extend(["--title", name])
    if body is not None: cmd.extend(["--notes", body])
    if draft is not None: cmd.append("--draft" if draft else "--draft=false")
    if prerelease is not None: cmd.append("--prerelease" if prerelease else "--prerelease=false")
    res = run_gh(cmd, timeout_s=30)
    if res["exit_code"] != 0: return json.dumps({"error": res["stderr"]}, ensure_ascii=False)
    return json.dumps({"id": release_id, "html_url": res["stdout"].strip(), "tag_name": tag_name, "name": name, "draft": draft, "prerelease": prerelease}, ensure_ascii=False)


@mcp.tool()
async def github_release_upload_asset(owner: str, repo: str, release_id: str, asset_path: str, label: str | None = None, content_type: str | None = None) -> str:
    """Upload a single asset to an existing release."""
    if not os.path.exists(asset_path): return json.dumps({"error": f"asset not found: {asset_path}"}, ensure_ascii=False)
    cmd = ["release", "upload", release_id, asset_path, "--repo", f"{owner}/{repo}"]
    if label: cmd.extend(["--label", label])
    res = run_gh(cmd, timeout_s=60)
    if res["exit_code"] != 0: return json.dumps({"error": res["stderr"]}, ensure_ascii=False)
    asset_name = os.path.basename(asset_path); size_bytes = os.path.getsize(asset_path)
    h = hashlib.sha256()
    with open(asset_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""): h.update(chunk)
    return json.dumps({"name": asset_name, "browser_download_url": res["stdout"].strip(), "sha256": h.hexdigest(), "size_bytes": size_bytes, "label": label}, ensure_ascii=False)


@mcp.tool()
async def github_release_delete(owner: str, repo: str, release_id: str, tag_name: str) -> str:
    """Delete a GitHub release. Requires EXACT tag_name match as confirmation."""
    verify = run_gh(["release", "view", release_id, "--repo", f"{owner}/{repo}", "--json", "tagName"], timeout_s=15)
    if verify["exit_code"] != 0: return json.dumps({"error": f"release {release_id} not found"}, ensure_ascii=False)
    try: data = json.loads(verify["stdout"]); actual_tag = data.get("tagName", "")
    except json.JSONDecodeError: actual_tag = ""
    if actual_tag != tag_name:
        return json.dumps({"error": f"tag_name '{tag_name}' does not match release {release_id}'s tag '{actual_tag}'. Must provide the exact tag name to confirm deletion."}, ensure_ascii=False)
    res = run_gh(["release", "delete", release_id, "--repo", f"{owner}/{repo}", "--yes"], timeout_s=30)
    return json.dumps({"deleted": res["exit_code"] == 0, "stderr": res["stderr"]}, ensure_ascii=False)


@mcp.tool()
async def github_release_delete_asset(owner: str, repo: str, asset_id: str, asset_name: str) -> str:
    """Remove an asset from a release. Requires EXACT asset_name match."""
    verify = run_gh(["api", f"/repos/{owner}/{repo}/releases/assets/{asset_id}", "--jq", ".name"], timeout_s=15)
    if verify["exit_code"] != 0: return json.dumps({"error": f"asset {asset_id} not found"}, ensure_ascii=False)
    actual_name = verify["stdout"].strip()
    if actual_name != asset_name:
        return json.dumps({"error": f"asset_name '{asset_name}' does not match asset {asset_id}'s name '{actual_name}'."}, ensure_ascii=False)
    res = run_gh(["api", f"/repos/{owner}/{repo}/releases/assets/{asset_id}", "-X", "DELETE"], timeout_s=30)
    return json.dumps({"deleted": res["exit_code"] == 0, "stderr": res["stderr"]}, ensure_ascii=False)


# -------------------------
# Workflow Management (E)
# -------------------------


@mcp.tool()
async def github_workflow_dispatch(owner: str, repo: str, workflow_id_or_filename: str, ref: str, inputs: dict[str, str] | None = None) -> str:
    """Trigger a workflow_dispatch-enabled GitHub Actions workflow."""
    cmd = ["workflow", "run", workflow_id_or_filename, "--repo", f"{owner}/{repo}", "--ref", ref]
    if inputs:
        for k, v in inputs.items(): cmd.extend(["-f", f"{k}={v}"])
    res = run_gh(cmd, timeout_s=30)
    if res["exit_code"] != 0: return json.dumps({"error": res["stderr"]}, ensure_ascii=False)
    time.sleep(1)
    list_res = run_gh(["run", "list", "--repo", f"{owner}/{repo}", "--limit", "1", "--workflow", workflow_id_or_filename, "--json", "databaseId,url,status"], timeout_s=15)
    run_id = None; run_url = None
    if list_res["exit_code"] == 0:
        try:
            runs = json.loads(list_res["stdout"])
            if runs: run_id = str(runs[0].get("databaseId", "")); run_url = runs[0].get("url", "")
        except json.JSONDecodeError: pass
    return json.dumps({"run_id": run_id, "html_url": run_url or res["stdout"].strip()}, ensure_ascii=False)


@mcp.tool()
async def github_workflow_run_wait(owner: str, repo: str, run_id: str, timeout_s: int = 600, poll_interval_s: int = 10) -> str:
    """Block until a workflow run reaches a terminal state."""
    elapsed = 0
    while elapsed < timeout_s:
        res = run_gh(["run", "view", run_id, "--repo", f"{owner}/{repo}", "--json", "status,conclusion,url"], timeout_s=15)
        if res["exit_code"] != 0: return json.dumps({"error": res["stderr"]}, ensure_ascii=False)
        try: data = json.loads(res["stdout"]); status = data.get("status", "unknown")
        except json.JSONDecodeError: status = "unknown"
        if status in {"completed", "cancelled", "skipped", "failed"}:
            return json.dumps({"status": status, "conclusion": data.get("conclusion"), "run_url": data.get("url", "")}, ensure_ascii=False)
        time.sleep(poll_interval_s); elapsed += poll_interval_s
    return json.dumps({"status": "timeout", "conclusion": "timed_out", "run_url": ""}, ensure_ascii=False)


@mcp.tool()
async def github_workflow_run_cancel(owner: str, repo: str, run_id: str) -> str:
    """Cancel an in-progress workflow run."""
    res = run_gh(["run", "cancel", run_id, "--repo", f"{owner}/{repo}"], timeout_s=30)
    return json.dumps({"cancelled": res["exit_code"] == 0, "stderr": res["stderr"]}, ensure_ascii=False)


@mcp.tool()
async def github_workflow_run_rerun(owner: str, repo: str, run_id: str, failed_only: bool = False) -> str:
    """Re-run a failed or cancelled workflow run."""
    cmd = ["run", "rerun", run_id, "--repo", f"{owner}/{repo}"]
    if failed_only: cmd.append("--failed")
    res = run_gh(cmd, timeout_s=30)
    return json.dumps({"success": res["exit_code"] == 0, "stderr": res["stderr"], "failed_only": failed_only}, ensure_ascii=False)


# -------------------------
# Check Runs, Branch Protection, PR Labels/Review (F)
# -------------------------


@mcp.tool()
async def github_check_runs_for_sha(owner: str, repo: str, ref: str) -> str:
    """List check runs against an arbitrary commit SHA."""
    res = run_gh(["api", f"/repos/{owner}/{repo}/commits/{ref}/check-runs", "--jq", ".check_runs[] | {name: .name, status: .status, conclusion: .conclusion}"], timeout_s=30)
    if res["exit_code"] != 0: return json.dumps({"error": res["stderr"]}, ensure_ascii=False)
    check_runs = []
    for line in res["stdout"].strip().splitlines():
        if not line: continue
        try: check_runs.append(json.loads(line))
        except json.JSONDecodeError: pass
    return json.dumps({"ref": ref, "check_runs": check_runs}, ensure_ascii=False)


@mcp.tool()
async def github_branch_protection_get(owner: str, repo: str, branch: str) -> str:
    """Read branch protection rules for a branch."""
    res = run_gh(["api", f"/repos/{owner}/{repo}/branches/{branch}", "--jq", "{protected: .protected, protection: .protection}"], timeout_s=15)
    if res["exit_code"] != 0: return json.dumps({"protected": False, "error": res["stderr"]}, ensure_ascii=False)
    try: data = json.loads(res["stdout"])
    except json.JSONDecodeError: return json.dumps({"protected": False}, ensure_ascii=False)
    protection = data.get("protection", {}) or {}
    rs = protection.get("required_status_checks", {}) or {}
    rpr = protection.get("required_pull_request_reviews", {}) or {}
    restr = protection.get("restrictions", {}) or {}
    return json.dumps({"protected": data.get("protected", False), "required_reviews": rpr.get("required_approving_review_count"), "required_status_checks": rs.get("contexts", []), "restrictions": restr}, ensure_ascii=False)


@mcp.tool()
async def github_pr_label_add(owner: str, repo: str, pull_number: int, labels: list[str]) -> str:
    """Add labels to a pull request."""
    cmd = ["pr", "edit", str(pull_number), "--repo", f"{owner}/{repo}"]
    for label in labels: cmd.extend(["--add-label", label])
    res = run_gh(cmd, timeout_s=30)
    if res["exit_code"] != 0: return json.dumps({"error": res["stderr"]}, ensure_ascii=False)
    label_res = run_gh(["pr", "view", str(pull_number), "--repo", f"{owner}/{repo}", "--json", "labels"], timeout_s=15)
    updated = []
    if label_res["exit_code"] == 0:
        try: data = json.loads(label_res["stdout"]); updated = [l["name"] for l in data.get("labels", [])]
        except json.JSONDecodeError: pass
    return json.dumps({"labels": updated}, ensure_ascii=False)


@mcp.tool()
async def github_pr_label_remove(owner: str, repo: str, pull_number: int, labels: list[str]) -> str:
    """Remove labels from a pull request."""
    cmd = ["pr", "edit", str(pull_number), "--repo", f"{owner}/{repo}"]
    for label in labels: cmd.extend(["--remove-label", label])
    res = run_gh(cmd, timeout_s=30)
    if res["exit_code"] != 0: return json.dumps({"error": res["stderr"]}, ensure_ascii=False)
    label_res = run_gh(["pr", "view", str(pull_number), "--repo", f"{owner}/{repo}", "--json", "labels"], timeout_s=15)
    updated = []
    if label_res["exit_code"] == 0:
        try: data = json.loads(label_res["stdout"]); updated = [l["name"] for l in data.get("labels", [])]
        except json.JSONDecodeError: pass
    return json.dumps({"labels": updated}, ensure_ascii=False)


@mcp.tool()
async def github_pr_request_review(owner: str, repo: str, pull_number: int, reviewers: list[str] | None = None, team_reviewers: list[str] | None = None) -> str:
    """Request review from users or teams on a pull request."""
    if not reviewers and not team_reviewers: return json.dumps({"error": "at least one of reviewers or team_reviewers required"}, ensure_ascii=False)
    cmd = ["pr", "edit", str(pull_number), "--repo", f"{owner}/{repo}"]
    for r in (reviewers or []): cmd.extend(["--add-reviewer", r])
    for t in (team_reviewers or []): cmd.extend(["--add-reviewer", f"@me/{t}"])
    res = run_gh(cmd, timeout_s=30)
    return json.dumps({"requested_reviewers": reviewers or [], "requested_teams": team_reviewers or [], "success": res["exit_code"] == 0, "stderr": res["stderr"]}, ensure_ascii=False)


@mcp.tool()
async def github_pr_auto_merge(owner: str, repo: str, pull_number: int, enable: bool, merge_method: str = "squash", commit_title: str | None = None, commit_message: str | None = None) -> str:
    """Toggle auto-merge on a pull request."""
    if enable:
        cmd = ["pr", "merge", str(pull_number), "--repo", f"{owner}/{repo}", "--auto", f"--{merge_method}"]
        if commit_title: cmd.extend(["--subject", commit_title])
        if commit_message: cmd.extend(["--body", commit_message])
    else:
        cmd = ["pr", "merge", str(pull_number), "--repo", f"{owner}/{repo}", "--disable-auto"]
    res = run_gh(cmd, timeout_s=30)
    return json.dumps({"auto_merge": enable, "method": merge_method if enable else None, "success": res["exit_code"] == 0, "stderr": res["stderr"]}, ensure_ascii=False)


# -------------------------
# Entry point
# -------------------------

def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
