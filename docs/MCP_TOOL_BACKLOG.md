# MCP Tool Backlog

> **Status as of 0.2.0 — mostly delivered.** This document was written as a gap analysis before the tools existed. Of the **35** tools it names, **28 ship under exactly that name**, **5 shipped under a merged or renamed API**, and **2 are still open**. The "these are missing" framing is therefore stale; the descriptions, input/output contracts and tiering rubric are not, which is why the document is kept rather than deleted.
>
> Delivered in 0.2.0: the Tier 1 release-flow tools, the `git-tools` expansion (22 → 34), the `github-tools` expansion (2 → 17), and the new `python-tools` server (7 tools). Package total: 61 → 95 tools across 7 servers.
>
> **Shipped under a different name than this document proposes:**
>
> | proposed here | shipped as |
> |---|---|
> | `github_release_publish`, `github_release_update` | `github_release_edit` (publish + update merged — a draft→published transition is just `draft: false`) |
> | `ruff_check` | `ruff` (check + format in one tool) |
> | `coverage_collect` | `coverage` (collect + report merged) |
> | `github_pr_enable_auto_merge` | `github_pr_auto_merge` (takes `enable: bool`, so it also disables) |
>
> **Still open:** `git_show_ref`, `uv_sync`.
>
> Read the sections below as the design record for the tools that exist, and as the remaining spec for the two that do not.

Gap analysis for development-flow MCP servers. Tools below are absent or only partially exposed in current MCP server inventories (custom `git-tools`, `github-tools`, and the official GitHub MCP server). Each item lists the tool, a generic description, the typical workflow that needs it, and a priority tier.

Tiering rubric:

- **Tier 1** — blocks core release/development flow. Without it, the agent must hand the step back to the human or bypass safety hooks.
- **Tier 2** — likely to be needed within the next release/sprint cycle for any project that ships artifacts and uses CI.
- **Tier 3** — quality-of-life and broader hygiene. Workable today via Bash; typed wrappers would let agents reason about results structurally.

---

## Tier 1 — Release-flow blockers

### `git_tag_create`

Create a git tag at a specified ref (annotated or lightweight). For annotated tags, accept a tagger identity and a message body. Should refuse to clobber an existing tag of the same name unless an explicit `force` flag is passed.

- **Inputs:** `repo_path`, `name`, `ref` (defaults to `HEAD`), `message` (annotated when present), `force` (default `false`), optional `sign`.
- **Outputs:** tag SHA (the tag-object SHA for annotated, or commit SHA for lightweight), exit code, stderr.
- **Workflow served:** "tag the squash-merge commit before pushing" — the canonical release sequence.

### `git_tag_delete`

Delete a tag locally. Pure-local deletion is safe and reversible (the tag can be recreated from a SHA), so this is distinct from a remote tag deletion which is destructive on shared state.

- **Inputs:** `repo_path`, `name`.
- **Outputs:** exit code.
- **Workflow served:** smoke-test failed after the local tag was created — recovery requires deleting the local-only tag before retrying. Without this, the agent must shell out or hand back to the user.

### `github_release_create`

Create a GitHub release. Must support draft mode and target a specific commit (`target_commitish`) so a release can be staged before its tag is published. Asset upload may be a separate call (see `github_release_upload_asset`); a convenience variant accepting an asset list is also valuable.

- **Inputs:** `owner`, `repo`, `tag_name`, `target_commitish`, `name`, `body` (or `body_path`), `draft` (default `true`), `prerelease` (default `false`), optional `assets` (list of local file paths).
- **Outputs:** release ID, html_url, draft state, list of uploaded asset names.
- **Workflow served:** every release of every artifact-shipping project. Currently the official GitHub MCP server exposes only read-side release tools (`get_release_by_tag`, `list_releases`); the write side is absent.

### `github_release_publish`

Transition a draft release to published. This is the step that fires email/RSS notifications and makes the tag's release page live, so it deserves its own primitive — agents should be able to gate it explicitly behind a smoke-test verdict or a human approval.

- **Inputs:** `owner`, `repo`, `release_id` (or `tag_name`).
- **Outputs:** published-at timestamp, html_url.

### `github_release_update`

Edit an existing release's body, title, draft state, or tag target. The first two are common: typo fixes in the body, draft → published transitions, retroactive prerelease flagging.

- **Inputs:** `owner`, `repo`, `release_id`, optional `body` / `name` / `draft` / `prerelease` / `tag_name` / `target_commitish`.
- **Outputs:** updated release object.

### `github_release_delete`

Delete a release (the GitHub release object only — does not touch the underlying git tag). Strictly destructive on a published release; agents should require explicit confirmation. Useful for backing out a draft that was created against the wrong tag, or for cleaning up a botched release before publishing.

- **Inputs:** `owner`, `repo`, `release_id`.
- **Outputs:** exit code.

### `github_release_upload_asset`

Upload a single asset to an existing release (draft or published). Splitting this from `github_release_create` lets agents build → smoke → upload in distinct steps, and lets them recover from a partial upload without having to recreate the release.

- **Inputs:** `owner`, `repo`, `release_id`, `asset_path`, optional `label` and `content_type`.
- **Outputs:** asset ID, browser_download_url, sha256 digest, byte size.

### `github_release_delete_asset`

Remove an asset from a release. Needed for backing out a corrupt upload before publishing, or for retroactively removing an asset that was discovered to contain a regression.

- **Inputs:** `owner`, `repo`, `asset_id`.
- **Outputs:** exit code.

---

## Tier 2 — Likely needed within 1–2 release cycles

### `git_describe`

Wraps `git describe --tags`. Lets agents derive a human-readable version-like string from any commit and verify "the latest tag is what we expect to be the latest tag." Cheap sanity check after merging a release-prep PR.

- **Inputs:** `repo_path`, optional `ref` (default `HEAD`), `tags` (default `true`), `dirty` (default `false`).
- **Outputs:** description string.

### `git_revert`

Create a revert commit for a given SHA. Distinct from `git reset` — this is the merge-safe path. Critical recovery primitive when a release-prep PR ships a regression and the fix path is "revert + cut a patch release."

- **Inputs:** `repo_path`, `ref`, optional `no_commit` (stage but don't commit), `mainline` (for revert of merge commits).
- **Outputs:** new commit SHA.

### `git_rebase`

Rebase the current branch onto another ref. Most useful in the form `pull --rebase` and "rebase release-prep branch onto latest main before final merge." Should refuse mid-rebase to invoke an interactive editor.

- **Inputs:** `repo_path`, `onto`, optional `upstream`, optional `autostash`.
- **Outputs:** exit code, conflict file list (if any), final HEAD SHA.

### `git_archive`

Wraps `git archive`. Produces a tarball or zip of a tree at a given ref. Useful for generating source distributions outside the package-manager flow, for security-sensitive snapshots ("what was at this tag, byte-for-byte"), or for vendoring a dependency at a pinned ref.

- **Inputs:** `repo_path`, `ref`, `format` (`tar` / `tar.gz` / `zip`), `output_path`, optional `prefix`.
- **Outputs:** output file path, byte size, sha256.

### `github_workflow_dispatch`

Trigger a `workflow_dispatch`-enabled GitHub Actions workflow. Lets agents kick off CI on demand (e.g., "rerun the test matrix against the release commit before tagging") without UI clicks.

- **Inputs:** `owner`, `repo`, `workflow_id_or_filename`, `ref`, optional `inputs` (dict).
- **Outputs:** triggered run ID, html_url.

### `github_workflow_run_wait`

Block until a specified workflow run reaches a terminal state. Replaces the polling loop most agents end up writing manually with `gh api … --jq .status`. Should accept a max-wait and surface the conclusion (`success` / `failure` / `cancelled` / `timed_out`).

- **Inputs:** `owner`, `repo`, `run_id`, optional `timeout_s` (default ~600), optional `poll_interval_s` (default ~10).
- **Outputs:** final status, conclusion, run URL.

### `github_workflow_run_rerun`

Re-run a failed or cancelled workflow run. Two variants are common: rerun-all-jobs and rerun-failed-only.

- **Inputs:** `owner`, `repo`, `run_id`, optional `failed_only` (default `false`).
- **Outputs:** new run ID, html_url.

### `github_workflow_run_cancel`

Cancel an in-progress workflow run. Recovery primitive for stuck or runaway runs.

- **Inputs:** `owner`, `repo`, `run_id`.
- **Outputs:** exit code.

### `github_check_runs_for_sha`

List check runs against an arbitrary commit SHA, not only against a PR head. Useful when verifying a direct-to-default-branch commit (docs close-outs, dependabot merges) actually passed CI.

- **Inputs:** `owner`, `repo`, `ref`.
- **Outputs:** list of check runs with status + conclusion.

### `python_smoke_install`

Macro: create a throwaway venv, install a wheel into it, run a configured command (typically `--version` or `--help`), capture stdout and exit code, then tear down the venv. Returns a single typed result. Removes the recurring footgun of `uv venv` not bundling pip and the cross-platform `Scripts/` vs `bin/` divergence.

- **Inputs:** `wheel_path`, optional `python_version` (default whatever the wheel allows), `commands` (list of strings to run), `cleanup` (default `true`).
- **Outputs:** per-command stdout/exit-code map; final cleanup status.

### `wheel_inspect`

Read METADATA, RECORD, dist-info version, and entry points from a wheel without installing it. Lets a release flow assert that the wheel's metadata version matches the git tag before publishing assets.

- **Inputs:** `wheel_path`.
- **Outputs:** name, version, requires_python, entry_points, list of files (or RECORD digests).

### `sdist_inspect`

Same shape as `wheel_inspect` for source distributions. Reads `PKG-INFO` and the file manifest.

- **Inputs:** `sdist_path`.
- **Outputs:** name, version, file list.

---

## Tier 3 — Quality-of-life and broader hygiene

### `pytest_run`

Run pytest with structured output. Returns a typed object with passed / failed / skipped / xfailed / xpassed / deselected counts, plus an array of failed-test identifiers. Strictly more useful than tail-grepping the human summary line, which is brittle to format changes and lossy when output is captured through pipes.

- **Inputs:** `repo_path`, optional `paths` (defaults to `tests`), optional `markers`, optional `keyword`, optional `extra_args`.
- **Outputs:** counts dict, failures list, exit code, duration_s.

### `ruff_check` / `ruff_format`

Typed wrappers around `ruff check` and `ruff format`. Return a structured violations list and a per-file "would change" / "did change" verdict. Useful for review-loop agents that want to suggest specific fixes.

- **Inputs:** `repo_path`, optional `paths`, optional `fix` (apply changes, default `false`), optional `unsafe_fixes`.
- **Outputs:** violations list (file, line, code, message), changed-file list, exit code.

### `uv_build`

Wraps `uv build`. Cleans `dist/` first when asked, builds wheel + sdist, and returns the produced filenames + sizes + sha256. Removes the need for separate `rm -rf dist/` + `uv build` + `ls dist/` calls.

- **Inputs:** `repo_path`, optional `clean` (default `true`), optional `targets` (default `wheel,sdist`).
- **Outputs:** list of artifacts with path / size / sha256.

### `uv_sync` and `uv_lock`

Typed wrappers. Return resolver decisions or unchanged-state verdicts so agents can reason about whether a dep change actually moved the lock.

- **Inputs:** `repo_path`, optional `extras`, optional `dev` flag.
- **Outputs:** resolver summary, list of upgraded/downgraded packages, exit code.

### `git_config_get` and `git_config_set`

Read or write a single git config key at the repository, global, or system scope. Most-needed cases: reading `user.email`, setting branch upstream, toggling per-repo signing config. Strictly avoid exposing wholesale config rewriting; one key per call.

- **Inputs:** `repo_path`, `key`, `scope` (`local` / `global` / `system`), optional `value` (set form only).
- **Outputs:** value (get form), exit code.

### `git_show_ref`

Wraps `git show-ref` and `git for-each-ref`. Lets agents enumerate tags, branches, or arbitrary refs with their target SHAs without parsing `git log` or directory listings.

- **Inputs:** `repo_path`, optional `pattern`, optional `kind` (`tags` / `heads` / `all`).
- **Outputs:** list of `{name, sha, type}` records.

### `git_branch_create`

Create a branch at a ref without checking it out. Distinct from `git_checkout(create=true)` because branch creation and switching working-tree contents are independent operations and conflating them invites footguns when the working tree is dirty.

- **Inputs:** `repo_path`, `name`, optional `ref` (default `HEAD`), optional `track`.
- **Outputs:** new branch name, target SHA.

### `git_restore`

Wraps `git restore` (modern replacement for `git checkout -- <path>`). Reverts working-tree changes for one or more files without affecting the index or HEAD. Recovery primitive for "I edited the wrong file" situations.

- **Inputs:** `repo_path`, `paths`, optional `staged` (also restore index), optional `source` (ref to restore from).
- **Outputs:** exit code, list of restored files.

### `git_clean_dry_run`

Wraps `git clean --dry-run -fd`. Lists what would be removed without removing anything. The actual `git clean -fd` is destructive and should remain a Bash operation that requires user confirmation; the dry-run is read-only and useful for pre-flight checks.

- **Inputs:** `repo_path`, optional `paths`.
- **Outputs:** list of files/directories that would be removed.

### `git_reflog`

Read the reflog for HEAD or a specific ref. Recovery aid for "my branch is in a weird state and I want to find what it pointed at five operations ago."

- **Inputs:** `repo_path`, optional `ref` (default `HEAD`), optional `limit`.
- **Outputs:** list of reflog entries with index, action, message, SHA.

### `github_branch_protection_get`

Read the branch protection ruleset for a branch. Lets agents detect "main is protected; do not attempt direct push" before learning it from a failed push.

- **Inputs:** `owner`, `repo`, `branch`.
- **Outputs:** ruleset object (required reviews, required status checks, restrictions).

### `github_pr_label_add` and `github_pr_label_remove`

Add or remove labels on an existing PR. Enables release-train and triage workflows ("label `release-blocker` if any check fails").

- **Inputs:** `owner`, `repo`, `pull_number`, `labels` (list).
- **Outputs:** updated label list.

### `github_pr_request_review`

Request review from one or more users or teams on an open PR. Useful when an agent opens a PR on the user's behalf and needs to assign a reviewer.

- **Inputs:** `owner`, `repo`, `pull_number`, `reviewers` (users), `team_reviewers` (teams).
- **Outputs:** updated review-request list.

### `github_pr_enable_auto_merge` / `disable_auto_merge`

Toggle auto-merge on a PR with a chosen merge method (merge / squash / rebase). Lets the agent set up "merge after CI passes" without having to poll and manually merge.

- **Inputs:** `owner`, `repo`, `pull_number`, `merge_method`, optional `commit_title`, optional `commit_message`.
- **Outputs:** auto-merge state, configured method.

### `coverage_collect` and `coverage_report`

Run a coverage-instrumented test suite and produce a typed coverage summary. Returns total / per-file percentages, missing lines, and a JSON-friendly dump suitable for trend tracking.

- **Inputs:** `repo_path`, optional `paths`, optional `min_coverage` (fail if below).
- **Outputs:** total %, per-file map, missing-lines list, exit code.

---

## Design Conventions Worth Standardizing

These shape *how* the tools above should be exposed, independent of which ones get built first.

### Splitting compound operations

Make atomic primitives the default and offer convenience macros separately. For example:

- `github_release_create` should not require the asset list. Provide `github_release_upload_asset` as a separate call.
- `git_tag_create` should not push. Provide `git_push` (which already exists) for the push step.

This lets workflows interleave verification gates between primitives — build → smoke → upload, not build-and-upload-and-hope.

### Read-side first

Where a write tool is destructive (release delete, asset delete, branch delete on a remote), ensure the read side is exposed first so agents can verify the target before acting.

### Typed results over text

Tools that wrap CLIs should return parsed structures, not raw stdout. If the underlying CLI changes its output format, the MCP wrapper absorbs the breakage and the agent's reasoning stays stable.

### Explicit force flags

Any tool that can clobber existing state (tags, branches, releases, assets) should accept an explicit `force` flag that defaults to `false`. The agent must opt into the destructive form, never reach it by default.

### Cross-platform venv awareness

Anywhere a tool spins up a Python venv (`python_smoke_install`, future test-runner sandboxes), abstract over `Scripts/` (Windows) vs `bin/` (POSIX) and the `uv venv` no-pip default. Surface a single `python_path` and `script_path` in the result.

### Recovery primitives are first-class

`git_tag_delete` (local), `git_revert`, `git_restore`, `github_release_delete` — these are not afterthoughts. Without them, the agent has no way to recover from its own mistakes inside the MCP envelope and must escalate to the user or to forbidden Bash escape hatches.

---

## Anti-recommendations

These are deliberately *not* on the list. They invite footguns that outweigh their use cases.

- `git_merge` (free-form merge into the current branch) — most projects use PR-only merges; exposing this MCP-side encourages bypass of the review gate.
- `git_cherry_pick` — same reasoning; rarely needed outside specific maintenance flows, and when it is needed the operator usually wants the interactive form.
- `git_apply` / `git_am` — patch application is a niche workflow and the MCP envelope adds little over Bash for the rare cases that need it.
- `git_clean -fd` (the actual destructive form) — keep this as a Bash operation behind explicit user approval. The dry-run variant is the safe MCP primitive.
- `pypi_publish` — publishing to a public package index is a destination-side write with broad blast radius. Keep it manual until project-specific authentication and scope-of-tokens conventions are settled.

---

## Where Each Tool Belongs

Suggested distribution across servers, given typical custom-MCP layouts:

| Server | Domain |
|---|---|
| `git-tools` (custom) | All `git_*` tools above. Local repository operations. |
| `github-tools` (custom or extension to existing) | All `github_*` tools above. The official GitHub MCP server is missing the entire release write-side and most workflow controls. |
| `python-tools` (new — does not currently exist) | `pytest_run`, `ruff_check`, `ruff_format`, `uv_build`, `uv_sync`, `uv_lock`, `python_smoke_install`, `wheel_inspect`, `sdist_inspect`, `coverage_collect`, `coverage_report`. |

The Python-tools server is the largest greenfield opportunity — none of these wrappers exist today, and they'd unblock structured reasoning over test, lint, and build outcomes across every Python project.
