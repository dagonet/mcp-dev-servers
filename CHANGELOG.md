# Changelog

All notable changes to `mcp-dev-servers` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `template-sync-tools`: **lossy-merge safety net on `three_way`**. After every merge the result is checked against base: any line present in `base`, untouched by the project, and kept by the template that does not appear **in order** in the merged output (so a stray copy elsewhere cannot mask a loss at its own position) is reported in a new `dropped_lines` list, flips `has_conflicts`/`conflict_count`, and appends a synthetic `<<<<<<< PROJECT (dropped by merge)` hunk — the merge cannot silently drop a line both sides kept. For a **conflict-free** merge of `.sh` content the tool additionally runs `bash -n`, plus `node --check` on every embedded `node -e` block (single- or double-quoted); a failure surfaces as `syntax_error` with the same conflict treatment (`syntax_checked` reports whether the check ran; a missing, unspawnable or timed-out `bash`/`node` is a graceful skip, never a silent pass). All `SequenceMatcher` uses now pass `autojunk=False` — the default classifies a line repeated more than `len(b)//100 + 1` times as junk on sequences ≥ 200 lines, which excluded every blank line from merges of `AGENT_TEAM.md` (1015 lines) and blinded the guard. Motivated by a "clean" merge that lost the closing `];` of a hook's `allowPatterns` array — hooks fail open, so the broken hook silently disabled enforcement. An insertion whose anchor line the other side replaced is likewise surfaced as an `insertion anchor lost` conflict rather than being relocated to the end of the file.
- `template-sync-tools`: **PROJECT-CUSTOM region awareness** — when BOTH the template and the project file carry `<!-- PROJECT-CUSTOM:BEGIN/END -->` markers, the region is treated as project-owned: `template_compute_status` reclassifies region-only project edits to `UP_TO_DATE` (and a CONFLICT whose content outside the region is identical collapses to `AUTO_UPDATE`); `template_apply_file(source="template")` splices the project's region into the applied template (`region_preserved` in the result); `three_way` merges exclude the region and reattach it to the merged output (`region_reattached`). Stored manifest hashes remain full-content — no migration. Single-sided or malformed markers fall back to legacy full-file behavior. (Toolkit downstream findings 2026-07-19, finding #2)
- `template-sync-tools`: `template_finalize_sync` now validates every `applied_files` entry before writing — `templateHash`/`templateRawHash`/`localHash` must be 64-char lowercase hex, `file_path` must be non-empty without `..` traversal; violations reject the whole call with the offending entries listed and the manifest untouched. (Finding #6 — hand-typed hashes silently corrupted a manifest)

### Fixed
- `template-sync-tools`: **three-way merge silently dropped lines**. `difflib` reports an insertion as an opcode with `i1 == i2`; the merge walk recorded it against base line `i1` — a line that is *retained*, not changed — and then emitted the inserted lines **in place of** it. Any insertion by either side deleted the line it was inserted before, while the result still came back `has_conflicts: false, conflict_count: 0`. Consumers lost a `## Backlog` heading, a `LOG_FILE=` assignment still referenced further down, and the closing `];` of a hook's embedded JS array. Insertions are now anchored before the base line instead of replacing it (identical insertions on both sides are still emitted once; differing ones still conflict). Measured over 3000 randomized three-way merges: 1427 silently lossy before, 0 after. (Consumer findings 2026-08-29 — panoscribe #1, penumbra, Yutraffic-Challenge)
- `template-sync-tools`: `template_compute_status` now discovers new **root-tracked `hooks/` files**. `_scan_template_files` walked only `templates/<variant>/`, so a newly added shared hook (`hooks/retro-ledger.sh`, `hooks/lib/git-cmd.sh`) never appeared in `new_template_files` — the consumer applied a `settings.json` referencing scripts it did not have, the git gates failed to source their lib, and every gate then exited 0. Discovery walks `<repo>/hooks/` recursively and reports manifest-absent paths in the manifest's own relative form. Resolution behaviour is unchanged. (panoscribe #2)
- `template-sync-tools`: `git show` output is decoded as **UTF-8** explicitly instead of the platform locale codec. On Windows a template em dash came back as `â€”` in the reconstructed merge base, and anyone pasting `auto_merged` back via `source="provided"` committed the mojibake. (Yutraffic-Challenge H5)
- `template-sync-tools`: `template_finalize_sync` **drops dead manifest entries** — an entry whose template file is gone *and* whose project file is gone is removed (previously every later `template_compute_status` re-reported the same resolved `template_deleted` count, forever). A new optional `deleted_files` param drops entries the project removed deliberately while the template still ships the file. Both are reported in `dropped_entries` / `files_dropped`. (Yutraffic-Challenge H4)
- `template-sync-tools`: `_write_file_atomic` writes with `newline=""` so template LF content lands as LF on Windows — previously every synced file arrived CRLF and produced line-ending churn in consumer diffs. (Finding #5)
- `git-tools`: 12 new tools — `git_tag_create`, `git_tag_delete`, `git_describe`, `git_archive`, `git_revert`, `git_rebase` (non-interactive only), `git_config_get`, `git_config_set` (with key allowlist), `git_branch_create`, `git_restore`, `git_clean_dry_run`, `git_reflog` — bringing `git-tools` from 22 to 34 tools. `git_push` now accepts a `tags` parameter for pushing tags alongside branches.
- `github-tools`: 15 new tools — `github_release_create`, `github_release_edit` (merged publish + update), `github_release_delete` (name-match guard), `github_release_upload_asset`, `github_release_delete_asset` (name-match guard), `github_workflow_dispatch`, `github_workflow_run_wait`, `github_workflow_run_rerun`, `github_workflow_run_cancel`, `github_check_runs_for_sha`, `github_branch_protection_get`, `github_pr_label_add`, `github_pr_label_remove`, `github_pr_request_review`, `github_pr_auto_merge` — bringing `github-tools` from 2 to 17 tools.
- `python-tools`: New MCP server with 7 tools — `wheel_inspect`, `sdist_inspect`, `python_smoke_install`, `uv_build`, `pytest_run`, `ruff` (check + format in one tool), `coverage` (merged collect + report). Registered as `mcp-python-tools` console script.
- Package total: 61 → 95 tools across 7 servers. ([PR #7](https://github.com/dagonet/mcp-dev-servers/pull/7))

### Changed
- README intro now leads with the outcome ("Give Claude Code N tools that…") and a worked example, ahead of the prerequisites/install sections. ([PR #3](https://github.com/dagonet/mcp-dev-servers/pull/3))
- README *Related Projects* now links to Open Brain v0.3.0 and describes the wiki + contradictions families. ([PR #5](https://github.com/dagonet/mcp-dev-servers/pull/5))

## [0.1.0] — 2026-04-21

Initial packaged release. ([PR #1](https://github.com/dagonet/mcp-dev-servers/pull/1))

### Added
- Python package layout under `src/mcp_dev_servers/` (was `src/*_mcp.py` at repo root).
- `pyproject.toml` with hatchling build backend, PEP 621 metadata, and 6 console-script entry points: `mcp-git-tools`, `mcp-github-tools`, `mcp-dotnet-tools`, `mcp-ollama-tools`, `mcp-rust-tools`, `mcp-template-sync-tools`.
- Optional install extras: `ollama` (pulls `httpx`), `git`, `github`, `dotnet`, `rust`, `template-sync` (cosmetic — document external-tool requirements), `dev` (pytest, build, twine).
- Smoke tests (`tests/test_smoke.py`): parametrized import + `main` callable + tool-count check for all 6 servers.
- `Development` and `Roadmap` sections in README.

### Changed
- Install path: `pip install "mcp-dev-servers[ollama] @ git+https://github.com/dagonet/mcp-dev-servers.git"` (PyPI publish pending). Old `git clone + pip install -r requirements.txt` flow is gone.
- Each module's `__main__` body extracted into `def main()` so console scripts can import and invoke it.

### Removed
- `requirements.txt` (superseded by `pyproject.toml`).
- Old `src/*_mcp.py` paths at repo root (modules moved into the package).

[Unreleased]: https://github.com/dagonet/mcp-dev-servers/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/dagonet/mcp-dev-servers/releases/tag/v0.1.0
