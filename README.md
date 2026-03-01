# MCP Python Tools

Custom [Model Context Protocol](https://modelcontextprotocol.io/) servers for Claude Code, built with [FastMCP](https://github.com/jlowin/fastmcp). Four servers providing 43 tools across git, GitHub, .NET, and Ollama domains.

## Servers

| Server | File | Tools | Description |
|--------|------|-------|-------------|
| **git-tools** | `src/git_mcp.py` | 16 | Git operations (status, diff, commit, branch, push, etc.) |
| **github-tools** | `src/github_mcp.py` | 2 | GitHub utilities not in the official GitHub MCP (repo detection, workflow listing) |
| **dotnet-tools** | `src/dotnet_mcp.py` | 19 | .NET build, test, NuGet, EF migrations, code quality, coverage |
| **ollama-tools** | `src/ollama_mcp.py` | 6 | Local Ollama LLM operations (health, warmup, compression, JSON extraction) |

## Prerequisites

Each server has its own external dependencies:

| Server | Requires |
|--------|----------|
| **git-tools** | [Git](https://git-scm.com/) installed and in PATH |
| **github-tools** | Git + [GitHub CLI](https://cli.github.com/) (`gh`) installed and authenticated |
| **dotnet-tools** | [.NET SDK](https://dotnet.microsoft.com/) 8.0+ |
| **ollama-tools** | [Ollama](https://ollama.com/) running locally |

All servers require Python 3.11+ and the packages in `requirements.txt`.

## Quick Start

```bash
git clone https://github.com/dagonet/mcp-python-tools.git
cd mcp-python-tools
python -m venv .venv
.venv/Scripts/activate      # Windows
# source .venv/bin/activate  # Linux/macOS
pip install -r requirements.txt
```

Register servers with `claude mcp add`:

```bash
# git-tools (user-level — works in every git repo)
claude mcp add --scope user --transport stdio git-tools \
  -- "/path/to/mcp-python-tools/.venv/Scripts/python" "/path/to/mcp-python-tools/src/git_mcp.py"

# github-tools (user-level)
claude mcp add --scope user --transport stdio github-tools \
  -e GH_PROMPT_DISABLED=1 \
  -- "/path/to/mcp-python-tools/.venv/Scripts/python" "/path/to/mcp-python-tools/src/github_mcp.py"

# ollama-tools (user-level — if running Ollama)
claude mcp add --scope user --transport stdio ollama-tools \
  -e OLLAMA_URL=http://127.0.0.1:11434 \
  -e OLLAMA_MODEL_FIRST_PASS=mistral:7b-instruct-q4_K_M \
  -e OLLAMA_MODEL_EXTRACT_JSON=qwen2.5:7b-instruct-q4_K_M \
  -- "/path/to/mcp-python-tools/.venv/Scripts/python" "/path/to/mcp-python-tools/src/ollama_mcp.py"

# dotnet-tools (project-level — only in .NET projects)
claude mcp add --scope project --transport stdio dotnet-tools \
  -- "/path/to/mcp-python-tools/.venv/Scripts/python" "/path/to/mcp-python-tools/src/dotnet_mcp.py"
```

Then grant tool permissions in your `settings.json` (user or project level):

```json
{
  "permissions": {
    "allow": [
      "mcp__git-tools__*",
      "mcp__github-tools__*",
      "mcp__dotnet-tools__*",
      "mcp__ollama-tools__*"
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
| dotnet-tools | Project | Only relevant in .NET projects |

## Environment Variables

| Variable | Server | Default |
|----------|--------|---------|
| `OLLAMA_URL` | ollama-tools | `http://127.0.0.1:11434` |
| `OLLAMA_MODEL_FIRST_PASS` | ollama-tools | `mistral:7b-instruct-q4_K_M` |
| `OLLAMA_MODEL_EXTRACT_JSON` | ollama-tools | `qwen2.5:7b-instruct-q4_K_M` |
| `GH_EXE` | github-tools | Auto-detected |

## Tool Reference

### git-tools (16 tools)

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

## JSON Configuration

As an alternative to `claude mcp add`, you can configure servers directly in `~/.claude.json` (user-level) or `.claude/mcp.json` (project-level):

```json
{
  "mcpServers": {
    "git-tools": {
      "command": "python",
      "args": ["G:/git/mcp-python-tools/src/git_mcp.py"]
    },
    "github-tools": {
      "command": "python",
      "args": ["G:/git/mcp-python-tools/src/github_mcp.py"]
    },
    "dotnet-tools": {
      "command": "python",
      "args": ["G:/git/mcp-python-tools/src/dotnet_mcp.py"]
    },
    "ollama-tools": {
      "command": "python",
      "args": ["G:/git/mcp-python-tools/src/ollama_mcp.py"],
      "env": {
        "OLLAMA_URL": "http://127.0.0.1:11434"
      }
    }
  }
}
```

Or using `uvx`:

```json
{
  "mcpServers": {
    "git-tools": {
      "command": "uvx",
      "args": ["--from", "mcp[cli]", "mcp", "run", "G:/git/mcp-python-tools/src/git_mcp.py"]
    }
  }
}
```

## Design Decisions

- **stdio transport**: All servers use stdio for Claude Code compatibility
- **Cross-platform**: Windows `CREATE_NO_WINDOW` flag prevents console popups; Unix process group handling for clean timeouts
- **No bash git**: `git_mcp.py` resolves `git.exe` directly to avoid `.cmd` wrapper issues on Windows
- **English locale**: dotnet-tools forces `DOTNET_CLI_UI_LANGUAGE=en` for consistent output parsing
- **Output limits**: Large outputs (diffs, logs) are truncated to prevent context overflow

## Related

- [ClaudeCodeSetup](https://github.com/dagonet/ClaudeCodeSetup) — Full Claude Code setup guide with MCP server registration, permissions, hooks, and project templates
