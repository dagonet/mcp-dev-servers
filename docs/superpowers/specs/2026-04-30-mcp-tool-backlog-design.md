# MCP Tool Backlog — Design Document

Derived from `MCP_TOOL_BACKLOG.md` gap analysis. This document specifies: which server each tool belongs to, whether it's required, API contracts, and cross-cutting design decisions. Implementation ordering is deferred to a separate plan.

---

## 1. Existing Landscape

| Server | File | Tools | Domain |
|---|---|---|---|
| git-tools | `git_mcp.py` | 22 | Full git lifecycle (status→push, worktree) |
| github-tools | `github_mcp.py` | 2 | `gh_repo_from_origin`, `gh_workflow_list` |
| Official GitHub MCP | (external) | ~50 | PRs, issues, files, commits — but **no release write-side**, no workflow dispatch/rerun/cancel/wait |
| python-tools | — | 0 | **Does not exist** |

**Overlap note:** github-tools' `gh_workflow_list` duplicates the official MCP's `gh_workflow_list`. Decision: github-tools **retains** workflow-domain ownership. The official MCP's workflow tools are read-only; github-tools builds the full read+write workflow surface. Accept the duplication for domain coherence.

---

## 2. Tool Assignments, Verdicts & API Contracts

### 2.1 git-tools — 11 new tools

#### Tier 1

**`git_tag_create`** — Required.
```
Inputs:  repo_path: str, name: str, ref: str = "HEAD",
         message: str | None = None, force: bool = False
Outputs: { tag_sha: str, name: str, annotated: bool }
Errors:  "tag '{name}' already exists. Use force=true to overwrite."
```
Wraps `git tag -a` (annotated when message present) or `git tag` (lightweight).

**`git_tag_delete`** — Required. Local-only, safe, reversible.
```
Inputs:  repo_path: str, name: str
Outputs: { deleted: bool }
Errors:  "tag '{name}' not found"
```

#### Tier 2

**`git_describe`** — Required.
```
Inputs:  repo_path: str, ref: str = "HEAD", tags: bool = True, dirty: bool = False
Outputs: { description: str }
```

**`git_revert`** — Required. Merge-safe recovery.
```
Inputs:  repo_path: str, ref: str, no_commit: bool = False, mainline: int | None = None
Outputs: { commit_sha: str | None }
```
When `no_commit=True`, returns None for commit_sha (changes staged only).

**`git_rebase`** — Conditional. Only if interactive-refusal is reliable.
```
Inputs:  repo_path: str, onto: str, upstream: str | None = None, autostash: bool = False
Outputs: { head_sha: str, conflicts: list[str] }
Errors:  "interactive rebase not supported via MCP"
```
Guard: set `GIT_SEQUENCE_EDITOR=true` and `GIT_EDITOR=false` in subprocess env. Scan args for `-i`/`--interactive` and refuse before execution.

**`git_archive`** — Required.
```
Inputs:  repo_path: str, ref: str, format: str = "tar.gz",
         output_path: str, prefix: str | None = None
Outputs: { path: str, size_bytes: int, sha256: str }
```

#### Tier 3

**`git_config_get`** — Required. Read-only, any key.
```
Inputs:  repo_path: str, key: str, scope: str = "local"
Outputs: { key: str, value: str, scope: str }
```

**`git_config_set`** — Conditional. Write with allowlist.
```
Inputs:  repo_path: str, key: str, value: str, scope: str = "local"
Outputs: { key: str, value: str, scope: str }
Errors:  "key '{key}' is not in the allowed set: [user.name, user.email, ...]"
```
Allowlist: `user.name`, `user.email`, `user.signingkey`, `commit.gpgsign`, `tag.gpgsign`, `branch.*.remote`, `branch.*.merge`, `push.default`, `pull.rebase`. Wildcard keys (branch.*) validated by prefix match.

**`git_branch_create`** — Required. No working-tree switch.
```
Inputs:  repo_path: str, name: str, ref: str = "HEAD", track: str | None = None
Outputs: { name: str, target_sha: str }
```

**`git_restore`** — Required. Recovery primitive.
```
Inputs:  repo_path: str, paths: list[str], staged: bool = False, source: str | None = None
Outputs: { restored_files: list[str] }
```

**`git_clean_dry_run`** — Required. Read-only preflight.
```
Inputs:  repo_path: str, paths: list[str] | None = None
Outputs: { would_remove: list[str] }
```

**`git_reflog`** — Conditional. Edge-case recovery.
```
Inputs:  repo_path: str, ref: str = "HEAD", limit: int = 20
Outputs: { entries: [{ index: int, sha: str, action: str, message: str }] }
```

**Cut from git-tools:** `git_show_ref` — existing `git_tag_list` + `git_branch_list` cover 80%.

**Tag push gap:** The existing `git_push` has a `branch` parameter. Passing a tag ref to `branch` is confusing but functionally works (`git push origin v1.0.0` pushes the tag). Decision: add optional `tags: bool` flag to `git_push` that appends `--tags`. No new tool needed. The `branch` parameter naming is unfortunate but changing it is a breaking change — defer to a future major version.

---

### 2.2 github-tools — 15 new tools

#### Tier 1 — Release write-side (6 tools, all Required)

The official GitHub MCP has `get_latest_release`, `get_release_by_tag`, `list_releases` (read-side). It has ZERO release write-side. These 6 tools fill that gap.

**`github_release_create`**
```
Inputs:  owner: str, repo: str, tag_name: str, target_commitish: str = "main",
         name: str | None = None, body: str = "", draft: bool = True,
         prerelease: bool = False
Outputs: { id: str, tag_name: str, name: str, html_url: str, draft: bool }
```
Does NOT accept assets. Assets go through `github_release_upload_asset`.

**`github_release_edit`** — Merges the former `publish` + `update` into one tool.
```
Inputs:  owner: str, repo: str, release_id: str,
         tag_name: str | None = None, target_commitish: str | None = None,
         name: str | None = None, body: str | None = None,
         draft: bool | None = None, prerelease: bool | None = None
Outputs: { id: str, html_url: str, ... all changed fields }
```
Pass `draft=false` to publish. Omit fields to leave unchanged. This eliminates the artificial publish/update split while still allowing workflow gating (the agent decides WHEN to pass `draft=false`).

**`github_release_delete`**
```
Inputs:  owner: str, repo: str, release_id: str, tag_name: str
Outputs: { deleted: bool }
Errors:  "tag_name '{given}' does not match release {id}'s tag '{actual}'.
         Must provide the exact tag name to confirm deletion."
```
No `confirm` boolean. The guard is: caller must provide the EXACT `tag_name` that matches the release. This forces an agent to look up the release first — a real verification step, not a trivially-passed boolean. Same pattern for `github_release_delete_asset` (must provide `asset_name`).

**`github_release_upload_asset`**
```
Inputs:  owner: str, repo: str, release_id: str, asset_path: str,
         label: str | None = None, content_type: str | None = None
Outputs: { id: str, name: str, browser_download_url: str,
           sha256: str, size_bytes: int }
```

**`github_release_delete_asset`**
```
Inputs:  owner: str, repo: str, asset_id: str, asset_name: str
Outputs: { deleted: bool }
Errors:  "asset_name '{given}' does not match asset {id}'s name '{actual}'."
```

#### Tier 2 — Workflow tools (5 tools, all Required)

**`github_workflow_dispatch`**
```
Inputs:  owner: str, repo: str, workflow_id_or_filename: str,
         ref: str, inputs: dict | None = None
Outputs: { run_id: str, html_url: str }
```

**`github_workflow_run_wait`**
```
Inputs:  owner: str, repo: str, run_id: str,
         timeout_s: int = 600, poll_interval_s: int = 10
Outputs: { status: str, conclusion: str | None, run_url: str }
```
Terminal states: `completed`, `cancelled`, `timed_out`, `skipped`, `failed` (our timeout). Polls `gh run view --json status,conclusion` at `poll_interval_s` until terminal or `timeout_s` elapsed.

**`github_workflow_run_rerun`**
```
Inputs:  owner: str, repo: str, run_id: str, failed_only: bool = False
Outputs: { new_run_id: str, html_url: str }
```

**`github_workflow_run_cancel`**
```
Inputs:  owner: str, repo: str, run_id: str
Outputs: { cancelled: bool }
```

**`github_check_runs_for_sha`**
```
Inputs:  owner: str, repo: str, ref: str
Outputs: { check_runs: [{ name: str, status: str, conclusion: str | None }] }
```

#### Tier 3 — PR hygiene (4 tools, all Required)

**`github_branch_protection_get`** — Required. Preflight before push.
```
Inputs:  owner: str, repo: str, branch: str
Outputs: { protected: bool, required_reviews: int | None,
           required_status_checks: list[str], restrictions: dict | None }
```

**`github_pr_label_add`** / **`github_pr_label_remove`** — Required. Atomic label operations; `issue_write` replaces all labels, can't do add/remove.
```
Inputs:  owner: str, repo: str, pull_number: int, labels: list[str]
Outputs: { labels: list[str] }  // updated label list
```

**`github_pr_request_review`** — Required.
```
Inputs:  owner: str, repo: str, pull_number: int,
         reviewers: list[str] | None = None, team_reviewers: list[str] | None = None
Outputs: { requested_reviewers: list[str], requested_teams: list[str] }
```

**`github_pr_auto_merge`** — Required. Single tool with `enable` boolean.
```
Inputs:  owner: str, repo: str, pull_number: int, enable: bool,
         merge_method: str = "squash", commit_title: str | None = None,
         commit_message: str | None = None
Outputs: { auto_merge: bool, method: str | None }
```

---

### 2.3 python-tools — NEW server, 6 tools

All tools use stdlib where possible. No new PyPI deps beyond `mcp[cli]`. Tools invoke `uv`, `pytest`, `ruff`, `coverage` as subprocesses — these are expected in the project environment, not bundled.

#### Tier 2 — Release verification (3 tools)

**`python_smoke_install`** — Required. Cross-platform venv abstraction.
```
Inputs:  wheel_path: str, commands: list[str],
         python_version: str | None = None, cleanup: bool = True
Outputs: { results: { command: { stdout: str, exit_code: int } },
           python_path: str, scripts_dir: str, cleaned_up: bool }
```
Platform detection: check `os.name == 'nt'` and `(venv_dir / 'Scripts').exists()` → `Scripts/`, else `bin/`. Uses `venv.create()` + `subprocess.run` for pip install and command execution. Teardown via `shutil.rmtree`.

**`wheel_inspect`** — Required. Uses `zipfile` stdlib.
```
Inputs:  wheel_path: str
Outputs: { name: str, version: str, requires_python: str | None,
           entry_points: dict, files: list[str] }
```

**`sdist_inspect`** — Required. Uses `tarfile` stdlib.
```
Inputs:  sdist_path: str
Outputs: { name: str, version: str, files: list[str] }
```

#### Tier 3 — Dev tools (3 tools)

**`pytest_run`** — Required. Typed test results.
```
Inputs:  repo_path: str, paths: list[str] | None = None,
         markers: str | None = None, keyword: str | None = None,
         extra_args: list[str] | None = None
Outputs: { passed: int, failed: int, skipped: int, xfailed: int,
           xpassed: int, deselected: int, exit_code: int, duration_s: float,
           failures: [{ name: str, message: str }] }
```
Parses `pytest --tb=short` or `--json-report` if plugin available. Falls back to exit-code-only if parsing fails.

**`ruff`** — Required. Single tool with `mode` param.
```
Inputs:  repo_path: str, mode: str = "check", paths: list[str] | None = None,
         fix: bool = False
Outputs (mode="check"): { violations: [{ file: str, line: int, code: str, message: str }],
                          exit_code: int }
Outputs (mode="format"): { changed_files: list[str], would_change: list[str],
                           exit_code: int }
```

**`uv_build`** — Required. Clean+build+collect in one call.
```
Inputs:  repo_path: str, clean: bool = True, targets: str = "wheel,sdist"
Outputs: { artifacts: [{ path: str, size_bytes: int, sha256: str }] }
```

**`coverage`** — Required. Single tool (merged collect + report).
```
Inputs:  repo_path: str, paths: list[str] | None = None,
         min_coverage: float | None = None
Outputs: { total_pct: float, per_file: { file: pct },
           missing_lines: { file: [line] }, exit_code: int }
```
Runs `coverage run -m pytest` then `coverage json`, reads `coverage.json`. If `min_coverage` set, exit code reflects pass/fail against threshold.

**Cut from python-tools:** `uv_sync`, `uv_lock` — thin CLI wrappers; agents can shell out.

---

## 3. Cross-Cutting Design Decisions

### 3.1 Deletion guards (not `confirm=true`)

`confirm=true` is trivially passed by any agent. Instead, deletion tools require the caller to provide an identifying attribute that forces a lookup:

| Tool | Guard |
|---|---|
| `github_release_delete` | Must pass `tag_name` matching the release |
| `github_release_delete_asset` | Must pass `asset_name` matching the asset |
| `git_tag_delete` | No guard needed (local-only, reversible) |
| `git_branch_delete` | Already exists with `force` flag |

### 3.2 Interactive-refusal for `git_rebase`

Two layers:
1. Before execution: scan args for `-i`/`--interactive` → refuse
2. During execution: set `GIT_SEQUENCE_EDITOR=true`, `GIT_EDITOR=false` in subprocess env → any attempt to spawn editor exits immediately

### 3.3 `git_config_set` allowlist

Only these keys are writable:
- Exact: `user.name`, `user.email`, `user.signingkey`, `commit.gpgsign`, `tag.gpgsign`, `push.default`, `pull.rebase`
- Wildcard prefix: `branch.*.remote`, `branch.*.merge`

All other keys rejected. `git_config_get` has no restrictions.

### 3.4 Release assets: no convenience param

`github_release_create` does NOT accept an `assets` list. The backlog's convenience variant is deferred. This enforces build→smoke→upload step separation. If demand proves the convenience variant necessary, add it later as a separate macro tool (not a param on `create`).

### 3.5 Workflow domain ownership

github-tools owns the full workflow surface (read + write). The official GitHub MCP's `gh_workflow_list` overlap is accepted. github-tools' `gh_workflow_list` already exists and stays. New workflow tools (dispatch, wait, rerun, cancel) go into github-tools alongside it.

### 3.6 Tag push

Existing `git_push` gets an optional `tags: bool = False` parameter that appends `--tags` to the push command. No new tool needed. The existing `branch` parameter name is unfortunate but not changed (breaking).

---

## 4. Summary

| Server | New Tools | Cut/Merged | Final Count |
|---|---|---|---|
| git-tools | +11 | `git_show_ref` cut | 33 total |
| github-tools | +15 | publish+update merged into `github_release_edit`, auto-merge merged into single tool | 17 total |
| python-tools | +6 (new) | `uv_sync`, `uv_lock` cut; ruff merged into one; coverage merged into one | 6 tools |
| **Total** | **32** | **5 cut/merged** | **56 across 3 servers** |

---

## 5. Verification Strategy

Layered approach:

1. **Namespace smoke test** — each server starts, all tools appear in `mcp__<server>__*` namespace
2. **Golden-path integration tests** — 8 end-to-end flows:
   - Tag create → list → force-guard → force → delete
   - Release create → edit (body) → edit (publish) → upload asset → delete asset → delete release (name-guard)
   - smoke_install: build wheel → install → run --version → verify cleanup
   - pytest_run: run against project tests → verify typed counts
   - config_set guard: disallowed key (rejected) → allowed key (accepted)
   - rebase guard: `-i` flag (rejected)
   - workflow_dispatch + run_wait + cancel
   - coverage: run under coverage → verify per-file percentages
3. **Regression guard** — `pytest tests/` must pass (existing tests unchanged)
4. **Deletion-guard tests** — attempt delete with wrong tag_name/asset_name (rejected), correct name (succeeds)
