# MCP Tool Backlog — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement 32 MCP tools (11 git, 15 github, 6 python) + 1 existing-tool enhancement across 3 servers

**Architecture:** Each server is a standalone FastMCP Python module. git-tools and github-tools extend existing files; python-tools is a new file. All tools follow the established `run_git()`/`run_gh()` helper patterns. The python-tools server uses `subprocess.run` directly with `_kill_process_tree` for timeout handling.

**Tech Stack:** Python 3.11+, FastMCP (`mcp.server.fastmcp`), `mcp[cli]`, git, gh CLI, stdlib (`zipfile`, `tarfile`, `venv`, `hashlib`, `subprocess`)

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `src/mcp_dev_servers/git_mcp.py` | Modify | +11 tools, +1 enhancement to `git_push` |
| `src/mcp_dev_servers/github_mcp.py` | Modify | +15 tools |
| `src/mcp_dev_servers/python_tools_mcp.py` | **Create** | New server: 6 tools + helpers + entry point |
| `pyproject.toml` | Modify | Add python-tools console_scripts entry + optional-dep |

## Existing Patterns (reference for all tasks)

**git-tools tool signature:**
```python
@mcp.tool()
async def git_xxx(repo_path: str, ...) -> str:
    """Docstring with Args/Returns."""
    # validation
    res = run_git([args], cwd=repo_path, timeout_s=N)
    return json.dumps({"exit_code": res["exit_code"], ...}, ensure_ascii=False)
```

**github-tools tool signature:**
```python
@mcp.tool()
async def github_xxx(owner: str, repo: str, ...) -> str:
    """Docstring with Args/Returns."""
    # validation
    res = run_gh([args], timeout_s=N)
    if res["exit_code"] != 0:
        return json.dumps({"error": res["stderr"]}, ensure_ascii=False)
    data = json.loads(res["stdout"])
    return json.dumps({...}, ensure_ascii=False)
```

---

## Workstream A: git-tools — Tier 1 Tags (2 tools)

### Task A1: `git_tag_create` and `git_tag_delete`

**File:** `src/mcp_dev_servers/git_mcp.py`
**Insert after:** `git_show` tool (line ~763), before the Worktree helpers section

- [ ] **Step 1: Add `git_tag_create`**

Insert after line 763 (`git_show` function), before the `# Worktree helpers` comment:

```python
@mcp.tool()
async def git_tag_create(
    repo_path: str,
    name: str,
    ref: str = "HEAD",
    message: str | None = None,
    force: bool = False,
) -> str:
    """
    Create a git tag at a specified ref (annotated or lightweight).

    For annotated tags, pass a message. Lightweight tags are created when
    no message is provided. Refuses to clobber an existing tag unless
    force=True.

    Args:
        repo_path: Path to the git repository
        name: Tag name
        ref: Target ref (commit hash, branch, tag, HEAD~N, etc.)
        message: If provided, create an annotated tag with this message
        force: If True, overwrite an existing tag of the same name

    Returns:
        JSON with tag_sha, name, annotated, exit_code, stderr
    """
    if not name:
        return json.dumps({"error": "tag name required"}, ensure_ascii=False)

    # Check for existing tag unless force
    if not force:
        existing = run_git(["tag", "-l", name], cwd=repo_path, timeout_s=10)
        if existing["stdout"].strip():
            return json.dumps(
                {"error": f"tag '{name}' already exists. Use force=true to overwrite."},
                ensure_ascii=False,
            )

    cmd = ["tag"]
    if force:
        cmd.append("-f")
    if message is not None:
        cmd.extend(["-a", "-m", message])
    cmd.extend([name, ref])

    res = run_git(cmd, cwd=repo_path, timeout_s=20)

    # Get the tag SHA (tag-object SHA for annotated, commit SHA for lightweight)
    tag_sha = None
    annotated = message is not None
    if res["exit_code"] == 0:
        sha_res = run_git(["rev-parse", f"refs/tags/{name}"], cwd=repo_path, timeout_s=10)
        if sha_res["exit_code"] == 0:
            tag_sha = sha_res["stdout"].strip()

    return json.dumps({
        "exit_code": res["exit_code"],
        "tag_sha": tag_sha,
        "name": name,
        "annotated": annotated,
        "stderr": res["stderr"],
    }, ensure_ascii=False)
```

- [ ] **Step 2: Add `git_tag_delete`**

Insert after `git_tag_create`:

```python
@mcp.tool()
async def git_tag_delete(repo_path: str, name: str) -> str:
    """
    Delete a local git tag.

    Local-only deletion — does not touch remotes. The tag can be
    recreated from a SHA if needed.

    Args:
        repo_path: Path to the git repository
        name: Tag name to delete

    Returns:
        JSON with deleted and exit_code
    """
    if not name:
        return json.dumps({"error": "tag name required"}, ensure_ascii=False)

    res = run_git(["tag", "-d", name], cwd=repo_path, timeout_s=10)

    return json.dumps({
        "exit_code": res["exit_code"],
        "deleted": res["exit_code"] == 0,
        "stderr": res["stderr"],
    }, ensure_ascii=False)
```

- [ ] **Step 3: Smoke test**

```bash
cd G:/git/mcp-dev-servers && python -c "
from mcp_dev_servers.git_mcp import mcp
import asyncio
# Verify tools appear in MCP namespace
print('Tools:', [t for t in dir(mcp) if not t.startswith('_')])
"
```

Expected: `git_tag_create` and `git_tag_delete` appear in the tool list.

- [ ] **Step 4: Commit**

```bash
git add src/mcp_dev_servers/git_mcp.py
git commit -m "feat(git-tools): add git_tag_create and git_tag_delete"
```

---

## Workstream B: git-tools — Tier 2 (4 tools)

### Task B1: `git_describe` and `git_archive`

**File:** `src/mcp_dev_servers/git_mcp.py`
**Insert after:** `git_tag_delete` (from Task A1)

- [ ] **Step 1: Add `git_describe`**

```python
@mcp.tool()
async def git_describe(
    repo_path: str,
    ref: str = "HEAD",
    tags: bool = True,
    dirty: bool = False,
) -> str:
    """
    Derive a human-readable version string from a commit.

    Wraps `git describe --tags`. Useful as a sanity check after merging
    a release-prep PR.

    Args:
        repo_path: Path to the git repository
        ref: Commit reference (default: HEAD)
        tags: If True, use --tags to include all tags
        dirty: If True, append -dirty when working tree is modified

    Returns:
        JSON with description string
    """
    cmd = ["describe"]
    if tags:
        cmd.append("--tags")
    if dirty:
        cmd.append("--dirty")
    cmd.append(ref)

    res = run_git(cmd, cwd=repo_path, timeout_s=10)

    return json.dumps({
        "exit_code": res["exit_code"],
        "description": res["stdout"].strip(),
        "stderr": res["stderr"],
    }, ensure_ascii=False)
```

- [ ] **Step 2: Add `git_archive`**

```python
import hashlib

@mcp.tool()
async def git_archive(
    repo_path: str,
    ref: str,
    output_path: str,
    format: str = "tar.gz",
    prefix: str | None = None,
) -> str:
    """
    Produce a tarball or zip of a tree at a given ref.

    Useful for generating source distributions outside the package-manager
    flow and for security-sensitive snapshots.

    Args:
        repo_path: Path to the git repository
        ref: Commit, tag, or branch to archive
        output_path: Full path for the output archive file
        format: One of "tar", "tar.gz", "zip"
        prefix: Optional directory prefix inside the archive

    Returns:
        JSON with output path, file size in bytes, and sha256 digest
    """
    valid_formats = {"tar", "tar.gz", "zip"}
    if format not in valid_formats:
        return json.dumps({"error": f"format must be one of {valid_formats}"}, ensure_ascii=False)

    cmd = ["archive"]
    if format == "tar.gz":
        cmd.extend(["--format=tar.gz"])
    elif format == "zip":
        cmd.extend(["--format=zip"])
    else:
        cmd.extend(["--format=tar"])

    if prefix:
        cmd.extend(["--prefix", prefix])

    cmd.extend(["--output", output_path, ref])

    res = run_git(cmd, cwd=repo_path, timeout_s=60)

    size_bytes = None
    sha256 = None
    if res["exit_code"] == 0:
        try:
            size_bytes = os.path.getsize(output_path)
            h = hashlib.sha256()
            with open(output_path, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
            sha256 = h.hexdigest()
        except OSError:
            pass

    return json.dumps({
        "exit_code": res["exit_code"],
        "path": output_path,
        "size_bytes": size_bytes,
        "sha256": sha256,
        "stderr": res["stderr"],
    }, ensure_ascii=False)
```

Note: Add `import hashlib` to the top of `git_mcp.py` (after `import json` at line 13).

- [ ] **Step 3: Commit**

```bash
git add src/mcp_dev_servers/git_mcp.py
git commit -m "feat(git-tools): add git_describe and git_archive"
```

### Task B2: `git_revert` and `git_rebase`

**File:** `src/mcp_dev_servers/git_mcp.py`
**Insert after:** `git_archive` (from Task B1)

- [ ] **Step 1: Add `git_revert`**

```python
@mcp.tool()
async def git_revert(
    repo_path: str,
    ref: str,
    no_commit: bool = False,
    mainline: int | None = None,
) -> str:
    """
    Create a revert commit for a given SHA.

    Merge-safe recovery primitive. Distinct from git_reset — this creates
    a new commit that undoes the target, preserving history.

    Args:
        repo_path: Path to the git repository
        ref: Commit SHA to revert
        no_commit: If True, stage changes without creating a commit
        mainline: For revert of merge commits, the parent number to consider
                  as mainline (usually 1)

    Returns:
        JSON with commit_sha (None when no_commit=True), exit_code, stderr
    """
    if not ref:
        return json.dumps({"error": "ref required"}, ensure_ascii=False)

    cmd = ["revert", "--no-edit"]
    if no_commit:
        cmd.append("--no-commit")
    if mainline is not None:
        cmd.extend(["-m", str(mainline)])
    cmd.append(ref)

    res = run_git(cmd, cwd=repo_path, timeout_s=30)

    commit_sha = None
    if res["exit_code"] == 0 and not no_commit:
        sha_res = run_git(["rev-parse", "HEAD"], cwd=repo_path, timeout_s=10)
        if sha_res["exit_code"] == 0:
            commit_sha = sha_res["stdout"].strip()

    return json.dumps({
        "exit_code": res["exit_code"],
        "commit_sha": commit_sha,
        "stderr": res["stderr"],
    }, ensure_ascii=False)
```

- [ ] **Step 2: Add `git_rebase` with interactive-refusal guard**

```python
@mcp.tool()
async def git_rebase(
    repo_path: str,
    onto: str,
    upstream: str | None = None,
    autostash: bool = False,
) -> str:
    """
    Rebase the current branch onto another ref (non-interactive only).

    Refuses interactive rebase. Sets GIT_SEQUENCE_EDITOR=true and
    GIT_EDITOR=false to prevent editor spawn. Scans for -i/--interactive
    flags and rejects them before execution.

    Args:
        repo_path: Path to the git repository
        onto: Target ref to rebase onto
        upstream: Optional upstream branch (default: current branch's upstream)
        autostash: If True, stash uncommitted changes before rebase

    Returns:
        JSON with head_sha, conflicts list, exit_code, stderr
    """
    if not onto:
        return json.dumps({"error": "onto ref required"}, ensure_ascii=False)

    # Interactive refusal check (belt)
    if "interactive" in onto.lower() or onto.startswith("-i"):
        return json.dumps(
            {"error": "interactive rebase not supported via MCP"},
            ensure_ascii=False,
        )

    cmd = ["rebase", "--onto", onto]
    if autostash:
        cmd.append("--autostash")
    if upstream:
        cmd.append(upstream)

    # Interactive refusal check (suspenders) — set editor env vars
    env = os.environ.copy()
    env.update({
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_PAGER": "cat",
        "PAGER": "cat",
        "LC_ALL": "C",
        "GIT_SEQUENCE_EDITOR": "true",
        "GIT_EDITOR": "false",
    })

    exe = _git_exe()
    full_cmd = [exe] + cmd
    start = time.time()

    p = subprocess.Popen(
        full_cmd,
        cwd=repo_path,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        shell=False,
        creationflags=_SUBPROCESS_FLAGS,
    )

    try:
        out, err = p.communicate(timeout=60)
        exit_code = p.returncode
        timed_out = False
    except subprocess.TimeoutExpired:
        _kill_process_tree(p.pid)
        exit_code = 124
        out = ""
        err = f"Timed out after 60s"
        timed_out = True

    conflicts = []
    if exit_code != 0 and out:
        for line in out.splitlines():
            if line.startswith("CONFLICT"):
                conflicts.append(line.strip())

    head_sha = None
    if exit_code == 0:
        sha_res = run_git(["rev-parse", "HEAD"], cwd=repo_path, timeout_s=10)
        if sha_res["exit_code"] == 0:
            head_sha = sha_res["stdout"].strip()

    return json.dumps({
        "exit_code": exit_code,
        "head_sha": head_sha,
        "conflicts": conflicts,
        "stderr": err,
        "timed_out": timed_out,
    }, ensure_ascii=False)
```

- [ ] **Step 3: Commit**

```bash
git add src/mcp_dev_servers/git_mcp.py
git commit -m "feat(git-tools): add git_revert and git_rebase with interactive-refusal guard"
```

---

## Workstream C: git-tools — Tier 3 + git_push enhancement (7 changes)

### Task C1: `git_config_get` and `git_config_set`

**File:** `src/mcp_dev_servers/git_mcp.py`
**Insert after:** `git_rebase` (from Task B2)

- [ ] **Step 1: Add allowlist constant and `git_config_get`**

```python
# Allowed keys for git_config_set (see design doc §3.3)
_CONFIG_SET_ALLOWLIST = {
    "user.name", "user.email", "user.signingkey",
    "commit.gpgsign", "tag.gpgsign", "push.default", "pull.rebase",
}
_CONFIG_SET_WILDCARD_PREFIXES = ("branch.",)


def _config_key_allowed(key: str) -> bool:
    """Check if a key is in the git_config_set allowlist."""
    if key in _CONFIG_SET_ALLOWLIST:
        return True
    for prefix in _CONFIG_SET_WILDCARD_PREFIXES:
        if key.startswith(prefix):
            return key.endswith(".remote") or key.endswith(".merge")
    return False


@mcp.tool()
async def git_config_get(
    repo_path: str,
    key: str,
    scope: str = "local",
) -> str:
    """
    Read a single git config key.

    Read-only, no restrictions on which keys can be read.

    Args:
        repo_path: Path to the git repository
        key: Config key (e.g., "user.email", "branch.main.remote")
        scope: One of "local", "global", "system"

    Returns:
        JSON with key, value, scope, exit_code
    """
    valid_scopes = {"local", "global", "system"}
    if scope not in valid_scopes:
        return json.dumps({"error": f"scope must be one of {valid_scopes}"}, ensure_ascii=False)
    if not key:
        return json.dumps({"error": "key required"}, ensure_ascii=False)

    res = run_git(["config", f"--{scope}", "--get", key], cwd=repo_path, timeout_s=10)

    return json.dumps({
        "exit_code": res["exit_code"],
        "key": key,
        "value": res["stdout"].strip() if res["exit_code"] == 0 else None,
        "scope": scope,
        "stderr": res["stderr"],
    }, ensure_ascii=False)
```

- [ ] **Step 2: Add `git_config_set`**

```python
@mcp.tool()
async def git_config_set(
    repo_path: str,
    key: str,
    value: str,
    scope: str = "local",
) -> str:
    """
    Set a single git config key (allowlisted keys only).

    Only a safe subset of keys can be written. See design doc for the
    full allowlist. Attempting to set any other key returns an error.

    Args:
        repo_path: Path to the git repository
        key: Config key to set
        value: Value to set
        scope: One of "local", "global", "system"

    Returns:
        JSON with key, value, scope, exit_code
    """
    valid_scopes = {"local", "global", "system"}
    if scope not in valid_scopes:
        return json.dumps({"error": f"scope must be one of {valid_scopes}"}, ensure_ascii=False)
    if not key:
        return json.dumps({"error": "key required"}, ensure_ascii=False)

    if not _config_key_allowed(key):
        return json.dumps(
            {"error": f"key '{key}' is not in the allowed set for git_config_set. "
                      f"Allowed keys: user.name, user.email, user.signingkey, "
                      f"commit.gpgsign, tag.gpgsign, push.default, pull.rebase, "
                      f"branch.*.remote, branch.*.merge"},
            ensure_ascii=False,
        )

    res = run_git(["config", f"--{scope}", key, value], cwd=repo_path, timeout_s=10)

    return json.dumps({
        "exit_code": res["exit_code"],
        "key": key,
        "value": value,
        "scope": scope,
        "stderr": res["stderr"],
    }, ensure_ascii=False)
```

- [ ] **Step 3: Commit**

```bash
git add src/mcp_dev_servers/git_mcp.py
git commit -m "feat(git-tools): add git_config_get and git_config_set with allowlist"
```

### Task C2: `git_branch_create`, `git_restore`, `git_clean_dry_run`, `git_reflog`

**File:** `src/mcp_dev_servers/git_mcp.py`
**Insert after:** `git_config_set` (from Task C1)

- [ ] **Step 1: Add all four tools**

```python
@mcp.tool()
async def git_branch_create(
    repo_path: str,
    name: str,
    ref: str = "HEAD",
    track: str | None = None,
) -> str:
    """
    Create a branch at a ref without checking it out.

    Distinct from git_checkout(create=True) — does not switch the
    working tree. Safe when the working tree is dirty.

    Args:
        repo_path: Path to the git repository
        name: New branch name
        ref: Start point for the new branch (default: HEAD)
        track: Remote branch to track (e.g., "origin/main")

    Returns:
        JSON with branch name and target SHA
    """
    if not name:
        return json.dumps({"error": "branch name required"}, ensure_ascii=False)

    cmd = ["branch", name, ref]
    if track:
        cmd.extend(["--track", track])

    res = run_git(cmd, cwd=repo_path, timeout_s=10)

    target_sha = None
    if res["exit_code"] == 0:
        sha_res = run_git(["rev-parse", name], cwd=repo_path, timeout_s=10)
        if sha_res["exit_code"] == 0:
            target_sha = sha_res["stdout"].strip()

    return json.dumps({
        "exit_code": res["exit_code"],
        "name": name,
        "target_sha": target_sha,
        "stderr": res["stderr"],
    }, ensure_ascii=False)


@mcp.tool()
async def git_restore(
    repo_path: str,
    paths: list[str],
    staged: bool = False,
    source: str | None = None,
) -> str:
    """
    Revert working-tree changes for one or more files.

    Wraps `git restore` — the modern replacement for `git checkout -- <path>`.
    Recovery primitive for "I edited the wrong file."

    Args:
        repo_path: Path to the git repository
        paths: List of file paths to restore
        staged: If True, also restore index (unstage changes)
        source: Ref to restore content from (default: HEAD)

    Returns:
        JSON with list of restored files
    """
    if not paths:
        return json.dumps({"error": "paths required"}, ensure_ascii=False)

    cmd = ["restore"]
    if staged:
        cmd.append("--staged")
    if source:
        cmd.extend(["--source", source])
    cmd.extend(["--"] + paths)

    res = run_git(cmd, cwd=repo_path, timeout_s=30)

    return json.dumps({
        "exit_code": res["exit_code"],
        "restored_files": paths,
        "stderr": res["stderr"],
    }, ensure_ascii=False)


@mcp.tool()
async def git_clean_dry_run(
    repo_path: str,
    paths: list[str] | None = None,
) -> str:
    """
    List files that `git clean -fd` would remove without removing them.

    Read-only preflight check. The actual `git clean -fd` is destructive
    and should remain a Bash operation requiring user confirmation.

    Args:
        repo_path: Path to the git repository
        paths: Optional list of paths to limit check to

    Returns:
        JSON with list of files/directories that would be removed
    """
    cmd = ["clean", "--dry-run", "-d", "-f"]
    if paths:
        cmd.extend(["--"] + paths)

    res = run_git(cmd, cwd=repo_path, timeout_s=10)

    would_remove = [p for p in res["stdout"].strip().splitlines() if p]

    return json.dumps({
        "exit_code": res["exit_code"],
        "would_remove": would_remove,
        "stderr": res["stderr"],
    }, ensure_ascii=False)


@mcp.tool()
async def git_reflog(
    repo_path: str,
    ref: str = "HEAD",
    limit: int = 20,
) -> str:
    """
    Read the reflog for HEAD or a specific ref.

    Recovery aid for finding what a branch pointed at before an
    unintended operation.

    Args:
        repo_path: Path to the git repository
        ref: Ref to read reflog for (default: HEAD)
        limit: Maximum number of entries to return

    Returns:
        JSON with list of reflog entries (index, sha, action, message)
    """
    res = run_git(
        ["reflog", f"-{limit}", "--format=%H|%gs", ref],
        cwd=repo_path,
        timeout_s=10,
    )

    entries = []
    for i, line in enumerate(res["stdout"].strip().splitlines()):
        if not line:
            continue
        parts = line.split("|", 1)
        entries.append({
            "index": i,
            "sha": parts[0] if parts else "",
            "message": parts[1] if len(parts) > 1 else "",
        })

    return json.dumps({
        "exit_code": res["exit_code"],
        "ref": ref,
        "entries": entries,
        "stderr": res["stderr"],
    }, ensure_ascii=False)
```

- [ ] **Step 2: Commit**

```bash
git add src/mcp_dev_servers/git_mcp.py
git commit -m "feat(git-tools): add git_branch_create, git_restore, git_clean_dry_run, git_reflog"
```

### Task C3: Add `tags` parameter to existing `git_push`

**File:** `src/mcp_dev_servers/git_mcp.py`
**Modify:** `git_push` function (lines ~568-607)

- [ ] **Step 1: Add `tags` parameter to `git_push`**

Edit the `git_push` function signature to add `tags: bool = False`:

```python
@mcp.tool()
async def git_push(repo_path: str, remote: str = "origin", branch: str | None = None,
                   set_upstream: bool = False, force: bool = False, delete: bool = False,
                   tags: bool = False) -> str:
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
        tags: If True, append --tags to push all tags alongside the branch.

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
    if tags:
        cmd.append("--tags")
    cmd.append(remote)
    if branch:
        cmd.append(branch)

    res = run_git(cmd, cwd=repo_path, timeout_s=120)

    return json.dumps({
        "exit_code": res["exit_code"],
        "stdout": res["stdout"],
        "stderr": res["stderr"],
    }, ensure_ascii=False)
```

- [ ] **Step 2: Commit**

```bash
git add src/mcp_dev_servers/git_mcp.py
git commit -m "feat(git-tools): add tags parameter to git_push for tag pushing"
```

---

## Workstream D: github-tools — Tier 1 Releases (5 tools)

### Task D1: `github_release_create`

**File:** `src/mcp_dev_servers/github_mcp.py`
**Insert after:** `gh_workflow_list` (line ~239), before the Entry point section

- [ ] **Step 1: Add `github_release_create`**

```python
@mcp.tool()
async def github_release_create(
    owner: str,
    repo: str,
    tag_name: str,
    target_commitish: str = "main",
    name: str | None = None,
    body: str = "",
    draft: bool = True,
    prerelease: bool = False,
) -> str:
    """
    Create a GitHub release. Defaults to draft mode.

    Does NOT accept assets — use github_release_upload_asset separately.
    Creates as draft by default so the agent can verify, smoke-test,
    and then publish via github_release_edit.

    Args:
        owner: Repository owner (username or organization)
        repo: Repository name
        tag_name: Tag name for the release
        target_commitish: Branch or commit SHA the tag points to
        name: Release title (defaults to tag_name)
        body: Release body/notes
        draft: If True, create as draft (default True)
        prerelease: If True, mark as prerelease

    Returns:
        JSON with release id, tag_name, name, html_url, draft state
    """
    if "/" in repo or "/" in owner:
        return json.dumps({"error": "provide owner and repo separately, not as owner/repo"}, ensure_ascii=False)

    cmd = [
        "release", "create", tag_name,
        "--repo", f"{owner}/{repo}",
        "--target", target_commitish,
        "--title", name or tag_name,
        "--notes", body or "",
    ]
    if draft:
        cmd.append("--draft")
    if prerelease:
        cmd.append("--prerelease")

    res = run_gh(cmd, timeout_s=30)

    if res["exit_code"] != 0:
        return json.dumps({"error": res["stderr"]}, ensure_ascii=False)

    # Get the release URL and ID from the output
    release_url = res["stdout"].strip()
    release_id = None
    if release_url:
        parts = release_url.rstrip("/").rsplit("/", 1)
        try:
            release_id = parts[-1] if parts[-1].isdigit() else None
        except (ValueError, IndexError):
            pass

    return json.dumps({
        "id": release_id,
        "tag_name": tag_name,
        "name": name or tag_name,
        "html_url": release_url,
        "draft": draft,
        "prerelease": prerelease,
    }, ensure_ascii=False)
```

- [ ] **Step 2: Commit**

```bash
git add src/mcp_dev_servers/github_mcp.py
git commit -m "feat(github-tools): add github_release_create"
```

### Task D2: `github_release_edit` and `github_release_upload_asset`

**File:** `src/mcp_dev_servers/github_mcp.py`
**Insert after:** `github_release_create` (from Task D1)

- [ ] **Step 1: Add `github_release_edit`**

```python
@mcp.tool()
async def github_release_edit(
    owner: str,
    repo: str,
    release_id: str,
    tag_name: str | None = None,
    target_commitish: str | None = None,
    name: str | None = None,
    body: str | None = None,
    draft: bool | None = None,
    prerelease: bool | None = None,
) -> str:
    """
    Edit an existing GitHub release.

    Pass draft=false to publish a draft release. Omit fields to leave
    them unchanged. Merges publish + update into one tool.

    Args:
        owner: Repository owner
        repo: Repository name
        release_id: Release ID to edit
        tag_name: New tag name (optional)
        target_commitish: New target commitish (optional)
        name: New release title (optional)
        body: New release body (optional)
        draft: Set draft state (optional). Pass false to publish
        prerelease: Set prerelease flag (optional)

    Returns:
        JSON with updated release URL
    """
    cmd = [
        "release", "edit", release_id,
        "--repo", f"{owner}/{repo}",
    ]
    if tag_name:
        cmd.extend(["--tag", tag_name])
    if target_commitish:
        cmd.extend(["--target", target_commitish])
    if name is not None:
        cmd.extend(["--title", name])
    if body is not None:
        cmd.extend(["--notes", body])
    if draft is not None:
        if draft:
            cmd.append("--draft")
        else:
            cmd.append("--draft=false")
    if prerelease is not None:
        if prerelease:
            cmd.append("--prerelease")
        else:
            cmd.append("--prerelease=false")

    res = run_gh(cmd, timeout_s=30)

    if res["exit_code"] != 0:
        return json.dumps({"error": res["stderr"]}, ensure_ascii=False)

    return json.dumps({
        "id": release_id,
        "html_url": res["stdout"].strip(),
        "tag_name": tag_name,
        "name": name,
        "draft": draft,
        "prerelease": prerelease,
    }, ensure_ascii=False)
```

- [ ] **Step 2: Add `github_release_upload_asset`**

```python
import hashlib

@mcp.tool()
async def github_release_upload_asset(
    owner: str,
    repo: str,
    release_id: str,
    asset_path: str,
    label: str | None = None,
    content_type: str | None = None,
) -> str:
    """
    Upload a single asset to an existing release.

    Split from github_release_create per design conventions:
    build -> smoke -> upload in distinct steps.

    Args:
        owner: Repository owner
        repo: Repository name
        release_id: Release ID to attach asset to
        asset_path: Local path to the file to upload
        label: Optional display label for the asset
        content_type: Optional MIME type (auto-detected if omitted)

    Returns:
        JSON with asset id, name, download URL, sha256, and file size
    """
    if not os.path.exists(asset_path):
        return json.dumps({"error": f"asset not found: {asset_path}"}, ensure_ascii=False)

    cmd = [
        "release", "upload", release_id, asset_path,
        "--repo", f"{owner}/{repo}",
    ]
    if label:
        cmd.extend(["--label", label])

    res = run_gh(cmd, timeout_s=60)

    if res["exit_code"] != 0:
        return json.dumps({"error": res["stderr"]}, ensure_ascii=False)

    asset_name = os.path.basename(asset_path)
    size_bytes = os.path.getsize(asset_path)
    h = hashlib.sha256()
    with open(asset_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)

    # Parse the upload URL from gh output
    upload_url = res["stdout"].strip()

    return json.dumps({
        "name": asset_name,
        "browser_download_url": upload_url,
        "sha256": h.hexdigest(),
        "size_bytes": size_bytes,
        "label": label,
    }, ensure_ascii=False)
```

Note: The `import hashlib` line should go at the top of the file with the other imports (after `import json` at line 14). Only add it once if not already present.

- [ ] **Step 3: Commit**

```bash
git add src/mcp_dev_servers/github_mcp.py
git commit -m "feat(github-tools): add github_release_edit and github_release_upload_asset"
```

### Task D3: `github_release_delete` and `github_release_delete_asset`

**File:** `src/mcp_dev_servers/github_mcp.py`
**Insert after:** `github_release_upload_asset` (from Task D2)

- [ ] **Step 1: Add `github_release_delete` with name-match guard**

```python
@mcp.tool()
async def github_release_delete(
    owner: str,
    repo: str,
    release_id: str,
    tag_name: str,
) -> str:
    """
    Delete a GitHub release (does not delete the underlying git tag).

    Destructive. Requires the EXACT tag_name matching the release as a
    guard — this forces the agent to look up the release first.

    Args:
        owner: Repository owner
        repo: Repository name
        release_id: Release ID to delete
        tag_name: Must match the release's actual tag name exactly

    Returns:
        JSON with deleted status
    """
    # Verify tag_name matches the release before deleting
    verify = run_gh(
        ["release", "view", release_id, "--repo", f"{owner}/{repo}",
         "--json", "tagName"],
        timeout_s=15,
    )
    if verify["exit_code"] != 0:
        return json.dumps({"error": f"release {release_id} not found"}, ensure_ascii=False)

    try:
        data = json.loads(verify["stdout"])
        actual_tag = data.get("tagName", "")
    except json.JSONDecodeError:
        actual_tag = ""

    if actual_tag != tag_name:
        return json.dumps(
            {"error": f"tag_name '{tag_name}' does not match release {release_id}'s "
                      f"tag '{actual_tag}'. Must provide the exact tag name to confirm deletion."},
            ensure_ascii=False,
        )

    res = run_gh(
        ["release", "delete", release_id, "--repo", f"{owner}/{repo}", "--yes"],
        timeout_s=30,
    )

    return json.dumps({
        "deleted": res["exit_code"] == 0,
        "stderr": res["stderr"],
    }, ensure_ascii=False)
```

- [ ] **Step 2: Add `github_release_delete_asset` with name-match guard**

```python
@mcp.tool()
async def github_release_delete_asset(
    owner: str,
    repo: str,
    asset_id: str,
    asset_name: str,
) -> str:
    """
    Remove an asset from a release.

    Destructive. Requires the EXACT asset_name as a guard — forces the
    agent to look up the asset first.

    Args:
        owner: Repository owner
        repo: Repository name
        asset_id: Asset ID to delete
        asset_name: Must match the asset's actual name exactly

    Returns:
        JSON with deleted status
    """
    # Verify asset_name matches via gh api
    verify = run_gh(
        ["api", f"/repos/{owner}/{repo}/releases/assets/{asset_id}", "--jq", ".name"],
        timeout_s=15,
    )
    if verify["exit_code"] != 0:
        return json.dumps({"error": f"asset {asset_id} not found"}, ensure_ascii=False)

    actual_name = verify["stdout"].strip()
    if actual_name != asset_name:
        return json.dumps(
            {"error": f"asset_name '{asset_name}' does not match asset {asset_id}'s "
                      f"name '{actual_name}'. Must provide the exact asset name."},
            ensure_ascii=False,
        )

    res = run_gh(
        ["api", f"/repos/{owner}/{repo}/releases/assets/{asset_id}",
         "-X", "DELETE"],
        timeout_s=30,
    )

    return json.dumps({
        "deleted": res["exit_code"] == 0,
        "stderr": res["stderr"],
    }, ensure_ascii=False)
```

- [ ] **Step 3: Commit**

```bash
git add src/mcp_dev_servers/github_mcp.py
git commit -m "feat(github-tools): add github_release_delete and github_release_delete_asset with name-match guards"
```

---

## Workstream E: github-tools — Tier 2 Workflows (5 tools)

### Task E1: `github_workflow_dispatch`

**File:** `src/mcp_dev_servers/github_mcp.py`
**Insert after:** `github_release_delete_asset` (from Task D3)

- [ ] **Step 1: Add `github_workflow_dispatch`**

```python
@mcp.tool()
async def github_workflow_dispatch(
    owner: str,
    repo: str,
    workflow_id_or_filename: str,
    ref: str,
    inputs: dict[str, str] | None = None,
) -> str:
    """
    Trigger a workflow_dispatch-enabled GitHub Actions workflow.

    Lets agents kick off CI on demand without UI clicks.

    Args:
        owner: Repository owner
        repo: Repository name
        workflow_id_or_filename: Workflow ID or filename (e.g., "ci.yml")
        ref: Branch or tag to run against
        inputs: Optional dict of workflow inputs

    Returns:
        JSON with triggered run ID and URL
    """
    cmd = [
        "workflow", "run", workflow_id_or_filename,
        "--repo", f"{owner}/{repo}",
        "--ref", ref,
    ]
    if inputs:
        for k, v in inputs.items():
            cmd.extend(["-f", f"{k}={v}"])

    res = run_gh(cmd, timeout_s=30)

    if res["exit_code"] != 0:
        return json.dumps({"error": res["stderr"]}, ensure_ascii=False)

    # Get the triggered run ID from a follow-up call
    time.sleep(1)  # Brief pause for API propagation
    list_res = run_gh(
        ["run", "list", "--repo", f"{owner}/{repo}", "--limit", "1",
         "--workflow", workflow_id_or_filename,
         "--json", "databaseId,url,status"],
        timeout_s=15,
    )
    run_id = None
    run_url = None
    if list_res["exit_code"] == 0:
        try:
            runs = json.loads(list_res["stdout"])
            if runs:
                run_id = str(runs[0].get("databaseId", ""))
                run_url = runs[0].get("url", "")
        except json.JSONDecodeError:
            pass

    return json.dumps({
        "run_id": run_id,
        "html_url": run_url or res["stdout"].strip(),
    }, ensure_ascii=False)
```

- [ ] **Step 2: Commit**

```bash
git add src/mcp_dev_servers/github_mcp.py
git commit -m "feat(github-tools): add github_workflow_dispatch"
```

### Task E2: `github_workflow_run_wait` and `github_workflow_run_cancel`

**File:** `src/mcp_dev_servers/github_mcp.py`
**Insert after:** `github_workflow_dispatch` (from Task E1)

- [ ] **Step 1: Add `github_workflow_run_wait`**

```python
@mcp.tool()
async def github_workflow_run_wait(
    owner: str,
    repo: str,
    run_id: str,
    timeout_s: int = 600,
    poll_interval_s: int = 10,
) -> str:
    """
    Block until a workflow run reaches a terminal state.

    Replaces the hand-rolled polling loop most agents write manually.

    Args:
        owner: Repository owner
        repo: Repository name
        run_id: Workflow run ID to wait for
        timeout_s: Maximum seconds to wait (default 600)
        poll_interval_s: Seconds between polls (default 10)

    Returns:
        JSON with final status, conclusion, and run URL
    """
    elapsed = 0
    while elapsed < timeout_s:
        res = run_gh(
            ["run", "view", run_id, "--repo", f"{owner}/{repo}",
             "--json", "status,conclusion,url"],
            timeout_s=15,
        )
        if res["exit_code"] != 0:
            return json.dumps({"error": res["stderr"]}, ensure_ascii=False)

        try:
            data = json.loads(res["stdout"])
            status = data.get("status", "unknown")
        except json.JSONDecodeError:
            status = "unknown"

        terminal = {"completed", "cancelled", "skipped", "failed"}
        if status in terminal:
            return json.dumps({
                "status": status,
                "conclusion": data.get("conclusion"),
                "run_url": data.get("url", ""),
            }, ensure_ascii=False)

        time.sleep(poll_interval_s)
        elapsed += poll_interval_s

    return json.dumps({
        "status": "timeout",
        "conclusion": "timed_out",
        "run_url": "",
    }, ensure_ascii=False)
```

- [ ] **Step 2: Add `github_workflow_run_cancel`**

```python
@mcp.tool()
async def github_workflow_run_cancel(
    owner: str,
    repo: str,
    run_id: str,
) -> str:
    """
    Cancel an in-progress workflow run.

    Recovery primitive for stuck or runaway runs.

    Args:
        owner: Repository owner
        repo: Repository name
        run_id: Workflow run ID to cancel

    Returns:
        JSON with cancelled status
    """
    res = run_gh(
        ["run", "cancel", run_id, "--repo", f"{owner}/{repo}"],
        timeout_s=30,
    )

    return json.dumps({
        "cancelled": res["exit_code"] == 0,
        "stderr": res["stderr"],
    }, ensure_ascii=False)
```

- [ ] **Step 3: Commit**

```bash
git add src/mcp_dev_servers/github_mcp.py
git commit -m "feat(github-tools): add github_workflow_run_wait and github_workflow_run_cancel"
```

### Task E3: `github_workflow_run_rerun` and `github_check_runs_for_sha`

**File:** `src/mcp_dev_servers/github_mcp.py`
**Insert after:** `github_workflow_run_cancel` (from Task E2)

- [ ] **Step 1: Add both tools**

```python
@mcp.tool()
async def github_workflow_run_rerun(
    owner: str,
    repo: str,
    run_id: str,
    failed_only: bool = False,
) -> str:
    """
    Re-run a failed or cancelled workflow run.

    Args:
        owner: Repository owner
        repo: Repository name
        run_id: Workflow run ID to re-run
        failed_only: If True, only re-run failed jobs

    Returns:
        JSON with new run ID and URL
    """
    cmd = ["run", "rerun", run_id, "--repo", f"{owner}/{repo}"]
    if failed_only:
        cmd.append("--failed")

    res = run_gh(cmd, timeout_s=30)

    return json.dumps({
        "success": res["exit_code"] == 0,
        "stderr": res["stderr"],
        "failed_only": failed_only,
    }, ensure_ascii=False)


@mcp.tool()
async def github_check_runs_for_sha(
    owner: str,
    repo: str,
    ref: str,
) -> str:
    """
    List check runs against an arbitrary commit SHA.

    Useful for verifying direct-to-default-branch commits (docs close-outs,
    Dependabot merges) actually passed CI. Not scoped to a PR.

    Args:
        owner: Repository owner
        repo: Repository name
        ref: Commit SHA to query check runs for

    Returns:
        JSON with list of check runs (name, status, conclusion)
    """
    res = run_gh(
        ["api", f"/repos/{owner}/{repo}/commits/{ref}/check-runs",
         "--jq", ".check_runs[] | {name: .name, status: .status, conclusion: .conclusion}"],
        timeout_s=30,
    )

    if res["exit_code"] != 0:
        return json.dumps({"error": res["stderr"]}, ensure_ascii=False)

    check_runs = []
    for line in res["stdout"].strip().splitlines():
        if not line:
            continue
        try:
            check_runs.append(json.loads(line))
        except json.JSONDecodeError:
            pass

    return json.dumps({
        "ref": ref,
        "check_runs": check_runs,
    }, ensure_ascii=False)
```

- [ ] **Step 2: Commit**

```bash
git add src/mcp_dev_servers/github_mcp.py
git commit -m "feat(github-tools): add github_workflow_run_rerun and github_check_runs_for_sha"
```

---

## Workstream F: github-tools — Tier 3 PR Hygiene (4 tools)

### Task F1: `github_branch_protection_get`

**File:** `src/mcp_dev_servers/github_mcp.py`
**Insert after:** `github_check_runs_for_sha` (from Task E3)

- [ ] **Step 1: Add `github_branch_protection_get`**

```python
@mcp.tool()
async def github_branch_protection_get(
    owner: str,
    repo: str,
    branch: str,
) -> str:
    """
    Read branch protection rules for a branch.

    Preflight check: lets agents detect "main is protected" before
    learning it from a failed push.

    Args:
        owner: Repository owner
        repo: Repository name
        branch: Branch name to check

    Returns:
        JSON with protected status, required reviews, status checks,
        and restrictions
    """
    res = run_gh(
        ["api", f"/repos/{owner}/{repo}/branches/{branch}",
         "--jq", "{protected: .protected, protection: .protection}"],
        timeout_s=15,
    )

    if res["exit_code"] != 0:
        # Branch may not exist or not have protection configured
        return json.dumps({"protected": False, "error": res["stderr"]}, ensure_ascii=False)

    try:
        data = json.loads(res["stdout"])
    except json.JSONDecodeError:
        return json.dumps({"protected": False}, ensure_ascii=False)

    protection = data.get("protection", {}) or {}
    required_status = protection.get("required_status_checks", {}) or {}
    required_pr = protection.get("required_pull_request_reviews", {}) or {}
    restrictions = protection.get("restrictions", {}) or {}

    return json.dumps({
        "protected": data.get("protected", False),
        "required_reviews": required_pr.get("required_approving_review_count"),
        "required_status_checks": required_status.get("contexts", []),
        "restrictions": restrictions,
    }, ensure_ascii=False)
```

- [ ] **Step 2: Commit**

```bash
git add src/mcp_dev_servers/github_mcp.py
git commit -m "feat(github-tools): add github_branch_protection_get"
```

### Task F2: `github_pr_label_add`, `github_pr_label_remove`, `github_pr_request_review`, `github_pr_auto_merge`

**File:** `src/mcp_dev_servers/github_mcp.py`
**Insert after:** `github_branch_protection_get` (from Task F1)

- [ ] **Step 1: Add all four tools**

```python
@mcp.tool()
async def github_pr_label_add(
    owner: str,
    repo: str,
    pull_number: int,
    labels: list[str],
) -> str:
    """
    Add labels to a pull request.

    Atomic add operation — unlike issue_write which replaces all labels.

    Args:
        owner: Repository owner
        repo: Repository name
        pull_number: Pull request number
        labels: List of label names to add

    Returns:
        JSON with updated label list
    """
    cmd = ["pr", "edit", str(pull_number), "--repo", f"{owner}/{repo}"]
    for label in labels:
        cmd.extend(["--add-label", label])

    res = run_gh(cmd, timeout_s=30)

    if res["exit_code"] != 0:
        return json.dumps({"error": res["stderr"]}, ensure_ascii=False)

    # Read back the updated labels
    label_res = run_gh(
        ["pr", "view", str(pull_number), "--repo", f"{owner}/{repo}",
         "--json", "labels"],
        timeout_s=15,
    )
    updated_labels = []
    if label_res["exit_code"] == 0:
        try:
            data = json.loads(label_res["stdout"])
            updated_labels = [l["name"] for l in data.get("labels", [])]
        except json.JSONDecodeError:
            pass

    return json.dumps({
        "labels": updated_labels,
    }, ensure_ascii=False)


@mcp.tool()
async def github_pr_label_remove(
    owner: str,
    repo: str,
    pull_number: int,
    labels: list[str],
) -> str:
    """
    Remove labels from a pull request.

    Args:
        owner: Repository owner
        repo: Repository name
        pull_number: Pull request number
        labels: List of label names to remove

    Returns:
        JSON with updated label list
    """
    cmd = ["pr", "edit", str(pull_number), "--repo", f"{owner}/{repo}"]
    for label in labels:
        cmd.extend(["--remove-label", label])

    res = run_gh(cmd, timeout_s=30)

    if res["exit_code"] != 0:
        return json.dumps({"error": res["stderr"]}, ensure_ascii=False)

    label_res = run_gh(
        ["pr", "view", str(pull_number), "--repo", f"{owner}/{repo}",
         "--json", "labels"],
        timeout_s=15,
    )
    updated_labels = []
    if label_res["exit_code"] == 0:
        try:
            data = json.loads(label_res["stdout"])
            updated_labels = [l["name"] for l in data.get("labels", [])]
        except json.JSONDecodeError:
            pass

    return json.dumps({
        "labels": updated_labels,
    }, ensure_ascii=False)


@mcp.tool()
async def github_pr_request_review(
    owner: str,
    repo: str,
    pull_number: int,
    reviewers: list[str] | None = None,
    team_reviewers: list[str] | None = None,
) -> str:
    """
    Request review from users or teams on a pull request.

    Args:
        owner: Repository owner
        repo: Repository name
        pull_number: Pull request number
        reviewers: List of GitHub usernames
        team_reviewers: List of team slugs

    Returns:
        JSON with requested reviewers and teams
    """
    if not reviewers and not team_reviewers:
        return json.dumps({"error": "at least one of reviewers or team_reviewers required"}, ensure_ascii=False)

    cmd = ["pr", "edit", str(pull_number), "--repo", f"{owner}/{repo}"]
    for r in (reviewers or []):
        cmd.extend(["--add-reviewer", r])
    for t in (team_reviewers or []):
        cmd.extend(["--add-reviewer", f"@me/{t}"])

    res = run_gh(cmd, timeout_s=30)

    return json.dumps({
        "requested_reviewers": reviewers or [],
        "requested_teams": team_reviewers or [],
        "success": res["exit_code"] == 0,
        "stderr": res["stderr"],
    }, ensure_ascii=False)


@mcp.tool()
async def github_pr_auto_merge(
    owner: str,
    repo: str,
    pull_number: int,
    enable: bool,
    merge_method: str = "squash",
    commit_title: str | None = None,
    commit_message: str | None = None,
) -> str:
    """
    Toggle auto-merge on a pull request.

    Single tool with enable boolean — replaces the original enable/disable pair.

    Args:
        owner: Repository owner
        repo: Repository name
        pull_number: Pull request number
        enable: True to enable auto-merge, False to disable
        merge_method: One of "merge", "squash", "rebase" (only used when enable=True)
        commit_title: Optional merge commit title
        commit_message: Optional merge commit body

    Returns:
        JSON with auto-merge state and configured method
    """
    if enable:
        cmd = [
            "pr", "merge", str(pull_number), "--repo", f"{owner}/{repo}",
            "--auto", f"--{merge_method}",
        ]
        if commit_title:
            cmd.extend(["--subject", commit_title])
        if commit_message:
            cmd.extend(["--body", commit_message])
    else:
        cmd = [
            "pr", "merge", str(pull_number), "--repo", f"{owner}/{repo}",
            "--disable-auto",
        ]

    res = run_gh(cmd, timeout_s=30)

    return json.dumps({
        "auto_merge": enable,
        "method": merge_method if enable else None,
        "success": res["exit_code"] == 0,
        "stderr": res["stderr"],
    }, ensure_ascii=False)
```

- [ ] **Step 2: Commit**

```bash
git add src/mcp_dev_servers/github_mcp.py
git commit -m "feat(github-tools): add PR label, review request, and auto-merge tools"
```

---

## Workstream G: python-tools — New Server (6 tools)

### Task G1: Create server scaffold

**Files:**
- Create: `src/mcp_dev_servers/python_tools_mcp.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Create `python_tools_mcp.py` with helpers**

```python
"""
Python Development MCP Server Tools

Tools for Python project workflows:
- Smoke install (venv creation + wheel install + command run)
- Wheel and sdist inspection
- pytest, ruff, uv build, and coverage wrappers
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import hashlib
import signal
import zipfile
import tarfile
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


def run_tool(cmd: list[str], cwd: str | None = None, timeout_s: int = 120) -> dict:
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
```

- [ ] **Step 2: Add pyproject.toml entries**

Add to `[project.optional-dependencies]`:
```toml
python-tools = []
```

Add to `[project.scripts]`:
```toml
mcp-python-tools = "mcp_dev_servers.python_tools_mcp:main"
```

Also update the description line to include Python:
```toml
description = "FastMCP servers for Claude Code: git, GitHub, .NET, Ollama, Rust, template-sync, Python"
```

- [ ] **Step 3: Commit**

```bash
git add src/mcp_dev_servers/python_tools_mcp.py pyproject.toml
git commit -m "feat(python-tools): create new python-tools MCP server scaffold"
```

### Task G2: `wheel_inspect` and `sdist_inspect` (stdlib, no external deps)

**File:** `src/mcp_dev_servers/python_tools_mcp.py`
**Insert after:** `run_tool` helper

- [ ] **Step 1: Add `wheel_inspect`**

```python
@mcp.tool()
async def wheel_inspect(wheel_path: str) -> str:
    """
    Read METADATA, version, and entry points from a wheel without installing it.

    Uses the stdlib zipfile module. No external dependencies.

    Args:
        wheel_path: Path to the .whl file

    Returns:
        JSON with name, version, requires_python, entry_points, and file list
    """
    if not os.path.exists(wheel_path):
        return json.dumps({"error": f"wheel not found: {wheel_path}"}, ensure_ascii=False)

    try:
        with zipfile.ZipFile(wheel_path, "r") as zf:
            file_list = zf.namelist()

            # Find the dist-info directory
            dist_info = None
            for name in file_list:
                if name.endswith(".dist-info/") or name.endswith(".dist-info/METADATA"):
                    dist_info = name.rsplit("/", 1)[0] if "/" in name else name
                    break
            if not dist_info:
                return json.dumps({"error": "no .dist-info directory found in wheel"}, ensure_ascii=False)

            # Read METADATA
            metadata_path = f"{dist_info}/METADATA"
            if metadata_path not in file_list:
                return json.dumps({"error": f"METADATA not found at {metadata_path}"}, ensure_ascii=False)

            metadata = zf.read(metadata_path).decode("utf-8")

            # Parse key fields from METADATA
            name = ""
            version = ""
            requires_python = None
            for line in metadata.splitlines():
                if line.startswith("Name:"):
                    name = line.split(":", 1)[1].strip()
                elif line.startswith("Version:"):
                    version = line.split(":", 1)[1].strip()
                elif line.startswith("Requires-Python:"):
                    requires_python = line.split(":", 1)[1].strip()

            # Read entry_points.txt if present
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

            return json.dumps({
                "name": name,
                "version": version,
                "requires_python": requires_python,
                "entry_points": entry_points,
                "files": file_list,
            }, ensure_ascii=False)

    except zipfile.BadZipFile as e:
        return json.dumps({"error": f"invalid zip file: {e}"}, ensure_ascii=False)
```

- [ ] **Step 2: Add `sdist_inspect`**

```python
@mcp.tool()
async def sdist_inspect(sdist_path: str) -> str:
    """
    Read PKG-INFO and file manifest from a source distribution.

    Uses the stdlib tarfile module. No external dependencies.

    Args:
        sdist_path: Path to the .tar.gz sdist file

    Returns:
        JSON with name, version, and file list
    """
    if not os.path.exists(sdist_path):
        return json.dumps({"error": f"sdist not found: {sdist_path}"}, ensure_ascii=False)

    try:
        with tarfile.open(sdist_path, "r:gz") as tf:
            members = tf.getnames()

            # Find PKG-INFO
            pkg_info = None
            for name in members:
                if name.endswith("/PKG-INFO") or name == "PKG-INFO":
                    pkg_info = name
                    break
            if not pkg_info:
                return json.dumps({"error": "PKG-INFO not found in sdist"}, ensure_ascii=False)

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

            return json.dumps({
                "name": pkg_name,
                "version": version,
                "files": members,
            }, ensure_ascii=False)

    except tarfile.TarError as e:
        return json.dumps({"error": f"invalid tar file: {e}"}, ensure_ascii=False)
```

- [ ] **Step 3: Commit**

```bash
git add src/mcp_dev_servers/python_tools_mcp.py
git commit -m "feat(python-tools): add wheel_inspect and sdist_inspect"
```

### Task G3: `python_smoke_install`

**File:** `src/mcp_dev_servers/python_tools_mcp.py`
**Insert after:** `sdist_inspect` (from Task G2)

- [ ] **Step 1: Add `python_smoke_install`**

```python
@mcp.tool()
async def python_smoke_install(
    wheel_path: str,
    commands: list[str],
    python_version: str | None = None,
    cleanup: bool = True,
) -> str:
    """
    Create a throwaway venv, install a wheel, run commands, capture results.

    Cross-platform: detects Scripts/ (Windows) vs bin/ (POSIX) automatically.

    Args:
        wheel_path: Path to the .whl file to smoke-test
        commands: List of commands to run (e.g., ["mcp-git-tools", "--help"])
        cleanup: If True, remove the venv after testing

    Returns:
        JSON with per-command results (stdout + exit_code), python_path,
        scripts_dir, and cleanup status
    """
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
        # Create venv
        import venv
        builder = venv.EnvBuilder(with_pip=True)
        builder.create(venv_dir)

        # Detect scripts directory
        if os.name == "nt" and os.path.exists(os.path.join(venv_dir, "Scripts")):
            scripts_dir = os.path.join(venv_dir, "Scripts")
            python_exe = os.path.join(scripts_dir, "python.exe")
        else:
            scripts_dir = os.path.join(venv_dir, "bin")
            python_exe = os.path.join(scripts_dir, "python3")
            if not os.path.exists(python_exe):
                python_exe = os.path.join(scripts_dir, "python")

        if not os.path.exists(python_exe):
            return json.dumps({"error": f"python not found in venv at {python_exe}"}, ensure_ascii=False)

        # Install wheel using pip from the venv
        pip_exe = os.path.join(scripts_dir, "pip.exe" if os.name == "nt" else "pip")
        if not os.path.exists(pip_exe):
            pip_exe = os.path.join(scripts_dir, "pip3")

        install_res = run_tool(
            [python_exe, "-m", "pip", "install", wheel_path],
            timeout_s=120,
        )
        if install_res["exit_code"] != 0:
            results["_install"] = {
                "stdout": install_res["stdout"],
                "exit_code": install_res["exit_code"],
                "stderr": install_res["stderr"],
            }
            return json.dumps({
                "error": "pip install failed",
                "results": results,
                "python_path": python_exe,
                "scripts_dir": scripts_dir,
            }, ensure_ascii=False)

        # Run each command
        for cmd_str in commands:
            cmd_path = os.path.join(scripts_dir, cmd_str.split()[0])
            if os.path.exists(cmd_path):
                resolved = [cmd_path] + cmd_str.split()[1:]
            else:
                resolved = cmd_str.split()

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

    return json.dumps({
        "results": results,
        "python_path": python_exe,
        "scripts_dir": scripts_dir,
        "cleaned_up": cleaned_up,
    }, ensure_ascii=False)
```

- [ ] **Step 2: Commit**

```bash
git add src/mcp_dev_servers/python_tools_mcp.py
git commit -m "feat(python-tools): add python_smoke_install with cross-platform venv support"
```

### Task G4: `uv_build`

**File:** `src/mcp_dev_servers/python_tools_mcp.py`
**Insert after:** `python_smoke_install` (from Task G3)

- [ ] **Step 1: Add `uv_build`**

```python
@mcp.tool()
async def uv_build(
    repo_path: str,
    clean: bool = True,
    targets: str = "wheel,sdist",
) -> str:
    """
    Build Python distribution artifacts with uv.

    Cleans dist/ when asked, builds wheel + sdist, returns artifact
    paths, sizes, and sha256 digests. Eliminates the multi-step
    `rm -rf dist/ && uv build && ls dist/` Bash dance.

    Args:
        repo_path: Path to the project root
        clean: If True, remove dist/ before building
        targets: Comma-separated build targets (default: "wheel,sdist")

    Returns:
        JSON with list of artifacts (path, size_bytes, sha256)
    """
    # Clean dist/ if requested
    dist_dir = os.path.join(repo_path, "dist")
    if clean and os.path.exists(dist_dir):
        try:
            shutil.rmtree(dist_dir)
        except OSError as e:
            return json.dumps({"error": f"failed to clean dist/: {e}"}, ensure_ascii=False)

    # Build
    res = run_tool(["uv", "build"], cwd=repo_path, timeout_s=180)
    if res["exit_code"] != 0:
        return json.dumps({
            "error": "uv build failed",
            "stderr": res["stderr"],
            "stdout": res["stdout"],
        }, ensure_ascii=False)

    # Collect artifacts
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
            artifacts.append({
                "path": fpath,
                "name": fname,
                "size_bytes": size_bytes,
                "sha256": h.hexdigest(),
            })

    return json.dumps({
        "artifacts": artifacts,
    }, ensure_ascii=False)
```

- [ ] **Step 2: Commit**

```bash
git add src/mcp_dev_servers/python_tools_mcp.py
git commit -m "feat(python-tools): add uv_build"
```

### Task G5: `pytest_run`, `ruff`, and `coverage`

**File:** `src/mcp_dev_servers/python_tools_mcp.py`
**Insert after:** `uv_build` (from Task G4)

- [ ] **Step 1: Add `pytest_run`**

```python
@mcp.tool()
async def pytest_run(
    repo_path: str,
    paths: list[str] | None = None,
    markers: str | None = None,
    keyword: str | None = None,
    extra_args: list[str] | None = None,
) -> str:
    """
    Run pytest with structured output.

    Returns typed test counts plus failure details instead of
    requiring agents to tail-grep the summary line.

    Args:
        repo_path: Path to the project root
        paths: Specific files/directories to test (default: tests/)
        markers: pytest -m flag value
        keyword: pytest -k flag value
        extra_args: Additional pytest arguments

    Returns:
        JSON with passed, failed, skipped, xfailed, xpassed,
        deselected counts, failures list, exit_code, and duration
    """
    cmd = ["pytest", "--tb=short"]
    if markers:
        cmd.extend(["-m", markers])
    if keyword:
        cmd.extend(["-k", keyword])
    if extra_args:
        cmd.extend(extra_args)
    if paths:
        cmd.extend(paths)
    else:
        cmd.append("tests/")

    res = run_tool(cmd, cwd=repo_path, timeout_s=600)
    stdout = res["stdout"]

    # Parse the pytest summary line
    # Format: "X passed, Y failed, Z skipped, ... in N.Ns"
    passed = failed = skipped = xfailed = xpassed = deselected = 0
    failures = []

    # Extract counts from the summary line
    import re
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

    # Parse failure details from the FAILURES section
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

    return json.dumps({
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "xfailed": xfailed,
        "xpassed": xpassed,
        "deselected": deselected,
        "exit_code": res["exit_code"],
        "duration_s": res["duration_s"],
        "failures": failures,
    }, ensure_ascii=False)
```

- [ ] **Step 2: Add `ruff` (check + format in one)**

```python
@mcp.tool()
async def ruff(
    repo_path: str,
    mode: str = "check",
    paths: list[str] | None = None,
    fix: bool = False,
) -> str:
    """
    Run ruff linter or formatter with structured output.

    Single tool replacing the original ruff_check + ruff_format pair.

    Args:
        repo_path: Path to the project root
        mode: "check" (lint) or "format"
        paths: Specific files/directories to check
        fix: If True, apply fixes (check mode only)

    Returns:
        For mode="check": violations list + exit_code
        For mode="format": changed_files / would_change + exit_code
    """
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

        return json.dumps({
            "violations": [
                {"file": v.get("filename", ""),
                 "line": v.get("location", {}).get("row", 0),
                 "code": v.get("code", ""),
                 "message": v.get("message", "")}
                for v in violations
            ],
            "exit_code": res["exit_code"],
        }, ensure_ascii=False)
    else:
        # format mode: check exit code + stdout for changed files
        changed_files = []
        for line in res["stdout"].splitlines():
            if line.startswith("Would reformat:"):
                pass
            elif line.strip() and not line.startswith(" "):
                changed_files.append(line.strip())

        return json.dumps({
            "changed_files": changed_files if res["exit_code"] != 0 else [],
            "would_change": changed_files if res["exit_code"] != 0 else [],
            "exit_code": res["exit_code"],
        }, ensure_ascii=False)
```

- [ ] **Step 3: Add `coverage`**

```python
@mcp.tool()
async def coverage(
    repo_path: str,
    paths: list[str] | None = None,
    min_coverage: float | None = None,
) -> str:
    """
    Run tests under coverage and return typed coverage summary.

    Single tool (merged coverage_collect + coverage_report). Runs
    `coverage run -m pytest` then `coverage json`, parses the result.

    Args:
        repo_path: Path to the project root
        paths: Specific test paths (default: tests/)
        min_coverage: If set, exit code reflects pass/fail against threshold

    Returns:
        JSON with total_pct, per-file percentages, missing lines per file,
        and exit_code
    """
    test_paths = paths or ["tests/"]

    # Run under coverage
    run_res = run_tool(
        ["coverage", "run", "-m", "pytest"] + test_paths,
        cwd=repo_path,
        timeout_s=600,
    )
    if run_res["exit_code"] != 0 and run_res["exit_code"] != 1:
        # exit_code 1 = test failures (expected), >1 = coverage itself failed
        return json.dumps({
            "error": f"coverage run failed with exit code {run_res['exit_code']}",
            "stderr": run_res["stderr"],
        }, ensure_ascii=False)

    # Generate JSON report
    json_res = run_tool(
        ["coverage", "json", "-o", "-"],
        cwd=repo_path,
        timeout_s=30,
    )
    if json_res["exit_code"] != 0:
        return json.dumps({
            "error": "coverage json failed",
            "stderr": json_res["stderr"],
        }, ensure_ascii=False)

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

    return json.dumps({
        "total_pct": total_pct,
        "per_file": per_file,
        "missing_lines": missing_lines,
        "exit_code": exit_code,
    }, ensure_ascii=False)
```

- [ ] **Step 4: Add entry point to end of file**

```python
# -------------------------
# Entry point
# -------------------------

def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Commit**

```bash
git add src/mcp_dev_servers/python_tools_mcp.py
git commit -m "feat(python-tools): add pytest_run, ruff, and coverage tools"
```

### Task G6: Add `import re` to python_tools_mcp.py

**File:** `src/mcp_dev_servers/python_tools_mcp.py`

- [ ] **Step 1: Add `import re` at top**

After `import json` (line ~13), add:
```python
import re
```

(The `pytest_run` tool uses `re.findall`.)

- [ ] **Step 2: Commit**

```bash
git add src/mcp_dev_servers/python_tools_mcp.py
git commit -m "fix(python-tools): add missing import re for pytest_run"
```

---

## Verification

### Minimal smoke test (after each workstream completes)

```bash
# Verify git-tools starts and registers new tools
python -c "from mcp_dev_servers.git_mcp import mcp; print('OK')"

# Verify github-tools starts and registers new tools
python -c "from mcp_dev_servers.github_mcp import mcp; print('OK')"

# Verify python-tools starts and registers new tools
python -c "from mcp_dev_servers.python_tools_mcp import mcp; print('OK')"
```

### Full integration test (after all workstreams complete)

```bash
pytest tests/ -v
```

Expected: existing tests pass, no regressions.

### Tool count verification

```bash
python -c "
from mcp_dev_servers.git_mcp import mcp as g
from mcp_dev_servers.github_mcp import mcp as gh
from mcp_dev_servers.python_tools_mcp import mcp as py
print(f'git-tools: expected ~33 tools')
print(f'github-tools: expected ~17 tools')
print(f'python-tools: expected 6 tools')
"
```

---

## Execution Order

Workstreams A-C (git-tools), D-F (github-tools), and G (python-tools) are **independent** — they modify different files. They can run in parallel.

Within each workstream, tasks are sequential (each builds on the previous).

**Recommended:** Dispatch all 3 workstreams as parallel agents, then run verification at the end.
