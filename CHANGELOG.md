# Changelog

All notable changes to `mcp-dev-servers` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
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
