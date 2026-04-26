# Changelog

All notable changes to `mcp-dev-servers` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `git-tools`: 3 worktree tools — `git_worktree_list`, `git_worktree_add`, `git_worktree_remove` — bringing `git-tools` from 19 to 22 tools and the package total from 58 to 61. ([PR #4](https://github.com/dagonet/mcp-dev-servers/pull/4))

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
