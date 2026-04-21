# MCP Dev Servers

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/dagonet/mcp-dev-servers)

Custom [Model Context Protocol](https://modelcontextprotocol.io/) servers for Claude Code, built with [FastMCP](https://github.com/jlowin/fastmcp). Six servers providing 58 tools across git, GitHub, .NET, Ollama, Rust, and template-sync domains.

## Servers

| Server | Console script | Module | Tools | Description |
|--------|----------------|--------|-------|-------------|
| **git-tools** | `mcp-git-tools` | `mcp_dev_servers.git_mcp` | 19 | Git operations (status, diff, commit, branch, push, fetch, reset, etc.) |
| **github-tools** | `mcp-github-tools` | `mcp_dev_servers.github_mcp` | 2 | GitHub utilities not in the official GitHub MCP (repo detection, workflow listing) |
| **dotnet-tools** | `mcp-dotnet-tools` | `mcp_dev_servers.dotnet_mcp` | 19 | .NET build, test, NuGet, EF migrations, code quality, coverage |
| **ollama-tools** | `mcp-ollama-tools` | `mcp_dev_servers.ollama_mcp` | 6 | Local Ollama LLM operations (health, warmup, compression, JSON extraction) |
| **rust-tools** | `mcp-rust-tools` | `mcp_dev_servers.rust_mcp` | 4 | Cargo build, test, clippy with structured diagnostics |
| **template-sync-tools** | `mcp-template-sync-tools` | `mcp_dev_servers.template_sync_mcp` | 8 | Template manifest, status, diff, merge, placeholder ops, cross-variant sync |

## Prerequisites

Each server has its own external dependencies:

| Server | Requires |
|--------|----------|
| **git-tools** | [Git](https://git-scm.com/) installed and in PATH |
| **github-tools** | Git + [GitHub CLI](https://cli.github.com/) (`gh`) installed and authenticated |
| **dotnet-tools** | [.NET SDK](https://dotnet.microsoft.com/) 8.0+ |
| **ollama-tools** | [Ollama](https://ollama.com/) running locally |
| **rust-tools** | [Rust toolchain](https://rustup.rs/) (cargo, rustc) |
| **template-sync-tools** | Git (for three-way merge ancestor lookup) |

All servers require Python 3.11+.

## Quick Start

Install directly from the GitHub repo (PyPI publishing is planned but not yet done — see [Roadmap](#roadmap)):

```bash
pip install "mcp-dev-servers[ollama] @ git+https://github.com/dagonet/mcp-dev-servers.git"
```

Once this package is on PyPI, the shorter form will also work:

```bash
pip install "mcp-dev-servers[ollama]"   # future — not yet published
```

**Available extras:** `ollama` (pulls `httpx`), `git`, `github`, `dotnet`, `rust`, `template-sync`, `dev` (for running tests). The non-`ollama` extras pull no Python packages today — they exist as documentation for which external tool each server expects (see [Prerequisites](#prerequisites)).

The package installs 6 console scripts (`mcp-git-tools`, `mcp-github-tools`, `mcp-dotnet-tools`, `mcp-ollama-tools`, `mcp-rust-tools`, `mcp-template-sync-tools`). Register them with `claude mcp add`:

```bash
# git-tools (user-level — works in every git repo)
claude mcp add --scope user --transport stdio git-tools -- mcp-git-tools

# github-tools (user-level)
claude mcp add --scope user --transport stdio github-tools \
  -e GH_PROMPT_DISABLED=1 \
  -- mcp-github-tools

# ollama-tools (user-level — if running Ollama)
claude mcp add --scope user --transport stdio ollama-tools \
  -e OLLAMA_URL=http://127.0.0.1:11434 \
  -e OLLAMA_MODEL_FIRST_PASS=mistral:7b-instruct-q4_K_M \
  -e OLLAMA_MODEL_EXTRACT_JSON=qwen2.5:7b-instruct-q4_K_M \
  -- mcp-ollama-tools

# rust-tools (user-level — works in every Rust project)
claude mcp add --scope user --transport stdio rust-tools -- mcp-rust-tools

# dotnet-tools (project-level — only in .NET projects)
claude mcp add --scope project --transport stdio dotnet-tools -- mcp-dotnet-tools

# template-sync-tools (user-level — template syncing for any project)
claude mcp add --scope user --transport stdio template-sync-tools -- mcp-template-sync-tools
```

> If `mcp-git-tools` isn't found on your PATH, install with [`pipx`](https://pipx.pypa.io/) so the scripts land in a PATH-resolvable location:
>
> ```bash
> pipx install "mcp-dev-servers[ollama] @ git+https://github.com/dagonet/mcp-dev-servers.git"
> ```
>
> Alternatively, pass the absolute path to each console script in your `claude mcp add` commands.

Then grant tool permissions in your `settings.json` (user or project level):

```json
{
  "permissions": {
    "allow": [
      "mcp__git-tools__*",
      "mcp__github-tools__*",
      "mcp__dotnet-tools__*",
      "mcp__ollama-tools__*",
      "mcp__rust-tools__*",
      "mcp__template-sync-tools__*"
    ]
  }
}
```

## Registration Strategy

| Server | Scope | Rationale |
|--------|-------|-----------|
| git-tools | User | Every git repo benefits from these tools |
| github-tools | User | Every GitHub repo benefits from these tools |
| ollama-tools | User | Cross-project if running Ollama |
| rust-tools | User | Every Rust project benefits from these tools |
| dotnet-tools | Project | Only relevant in .NET projects |
| template-sync-tools | User | Cross-project template syncing |

## Environment Variables

| Variable | Server | Default |
|----------|--------|---------|
| `OLLAMA_URL` | ollama-tools | `http://127.0.0.1:11434` |
| `OLLAMA_MODEL_FIRST_PASS` | ollama-tools | `mistral:7b-instruct-q4_K_M` |
| `OLLAMA_MODEL_EXTRACT_JSON` | ollama-tools | `qwen2.5:7b-instruct-q4_K_M` |
| `GH_EXE` | github-tools | Auto-detected |

## Tool Reference

### git-tools (19 tools)

| Tool | Description |
|------|-------------|
| `git_env_info` | Diagnostic info about git installation |
| `git_status` | Fast porcelain git status |
| `git_add` | Stage specific files |
| `git_rm` | Remove files from tracking |
| `git_commit` | Create a commit |
| `git_diff_summary` | Compact diffstat summary |
| `git_diff` | Full diff output |
| `git_log` | Recent commit history |
| `git_branch_list` | List branches |
| `git_checkout` | Checkout branch/tag/commit |
| `git_pull` | Pull from remote |
| `git_push` | Push to remote |
| `git_stash` | Stash operations (push/pop/list/drop/clear) |
| `git_remote_list` | List configured remotes |
| `git_tag_list` | List tags |
| `git_show` | Show commit details |
| `git_branch_delete` | Delete a local branch (safe against current branch) |
| `git_fetch` | Fetch from remote without merging |
| `git_reset` | Reset HEAD to ref (soft/mixed/hard) |

### github-tools (2 tools)

| Tool | Description |
|------|-------------|
| `gh_repo_from_origin` | Get OWNER/REPO from local git remote |
| `gh_workflow_list` | List GitHub Actions workflow runs |

### dotnet-tools (19 tools)

| Tool | Description |
|------|-------------|
| `build_and_extract_errors` | Build and extract structured errors/warnings |
| `run_tests_summary` | Run tests and parse TRX results |
| `analyze_namespace_conflicts` | Find duplicate type definitions |
| `nuget_list_outdated` | List outdated NuGet packages |
| `nuget_check_vulnerabilities` | Check for NuGet security vulnerabilities |
| `nuget_dependency_tree` | Full NuGet dependency tree |
| `parse_csproj` | Parse .csproj file structure |
| `analyze_project_references` | Analyze inter-project dependencies |
| `check_framework_compatibility` | Check target framework mismatches |
| `ef_migrations_status` | List EF Core migrations status |
| `ef_pending_migrations` | Check for pending EF migrations |
| `ef_dbcontext_info` | Get DbContext provider/connection info |
| `analyze_method_complexity` | Estimate cyclomatic complexity |
| `find_large_files` | Find files exceeding line count threshold |
| `find_god_classes` | Find classes with too many members |
| `parse_stack_trace` | Parse .NET stack traces |
| `parse_coverage_report` | Parse Cobertura coverage XML |
| `run_coverage` | Run tests with coverage collection |
| `map_dotnet_structure` | Map .NET project file structure |

### ollama-tools (6 tools)

| Tool | Description |
|------|-------------|
| `ollama_health` | Check Ollama server status |
| `ollama_list_models` | List available Ollama models |
| `warm_models` | Pre-load models for faster inference |
| `local_first_pass` | Compress text via local LLM |
| `extract_json` | Extract structured JSON from text |
| `map_project_structure` | Map directory structure |

### rust-tools (4 tools)

| Tool | Description |
|------|-------------|
| `cargo_env_info` | Diagnostic info about Rust/Cargo installation |
| `cargo_build` | Build with structured error/warning diagnostics |
| `cargo_test` | Run tests and return results |
| `cargo_clippy` | Lint with structured clippy diagnostics |

### template-sync-tools (8 tools)

| Tool | Description |
|------|-------------|
| `template_load_manifest` | Load and validate manifest (auto-migrates v1 to v2) |
| `template_compute_status` | Per-file sync status (UP_TO_DATE, PROJECT_CUSTOM, AUTO_UPDATE, CONFLICT) |
| `template_get_diff` | Unified diff with three-way merge support |
| `template_apply_file` | Apply template/provided content or skip, returns manifest entry |
| `template_finalize_sync` | Atomically write manifest after sync completes |
| `template_reverse_placeholders` | Deterministic reverse placeholder replacement (longest-first) |
| `template_check_cross_variant` | Check which variants share identical file content |
| `template_propagate_to_variants` | Write template-ready content to multiple variant directories |

## JSON Configuration

As an alternative to `claude mcp add`, you can configure servers directly in `~/.claude.json` (user-level) or `.claude/mcp.json` (project-level):

```json
{
  "mcpServers": {
    "git-tools": {
      "command": "mcp-git-tools"
    },
    "github-tools": {
      "command": "mcp-github-tools",
      "env": {
        "GH_PROMPT_DISABLED": "1"
      }
    },
    "dotnet-tools": {
      "command": "mcp-dotnet-tools"
    },
    "rust-tools": {
      "command": "mcp-rust-tools"
    },
    "ollama-tools": {
      "command": "mcp-ollama-tools",
      "env": {
        "OLLAMA_URL": "http://127.0.0.1:11434"
      }
    },
    "template-sync-tools": {
      "command": "mcp-template-sync-tools"
    }
  }
}
```

If `mcp-*` scripts aren't on your PATH, use the absolute path to the script (e.g. `"command": "/full/path/to/venv/bin/mcp-git-tools"`) or switch to [`pipx`](https://pipx.pypa.io/) which installs scripts in a PATH-resolvable location.

## Design Decisions

- **stdio transport**: All servers use stdio for Claude Code compatibility
- **Cross-platform**: Windows `CREATE_NO_WINDOW` flag prevents console popups; Unix process group handling for clean timeouts
- **No bash git**: `git_mcp.py` resolves `git.exe` directly to avoid `.cmd` wrapper issues on Windows
- **English locale**: dotnet-tools forces `DOTNET_CLI_UI_LANGUAGE=en` for consistent output parsing
- **Output limits**: Large outputs (diffs, logs) are truncated to prevent context overflow

## Roadmap

- **PyPI publishing.** The package is fully PyPI-ready (pyproject.toml, hatchling build, console scripts, smoke tests) but not yet uploaded. Once published, `pip install "mcp-dev-servers[ollama]"` will work without the git URL.
- **GitHub Actions.** Automated test runs on PRs and Trusted-Publishing-based releases on tag push are planned.

## Development

To contribute or run from source:

```bash
git clone https://github.com/dagonet/mcp-dev-servers.git
cd mcp-dev-servers
python -m venv .venv
.venv/Scripts/activate      # Windows
# source .venv/bin/activate  # Linux/macOS
pip install -e ".[ollama,dev]"
pytest tests/
```

`pip install -e` installs the package in editable mode, so the console scripts (`mcp-git-tools`, etc.) pick up your local edits immediately. `pytest tests/` runs the smoke tests that verify each server imports cleanly and registers the expected number of tools.

## Related Projects

Part of an ecosystem for AI-assisted development with Claude Code:

- [claude-code-toolkit](https://github.com/dagonet/claude-code-toolkit) -- Template system for bootstrapping projects with Claude Code configuration, MCP server setup, and cross-platform setup scripts
- [open-brain](https://github.com/dagonet/open-brain) -- Persistent memory MCP server that stores decisions, insights, and context across sessions
