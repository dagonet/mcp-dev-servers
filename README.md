# MCP Python Tools

Custom [Model Context Protocol](https://modelcontextprotocol.io/) servers for Claude Code, built with [FastMCP](https://github.com/jlowin/fastmcp). Four servers providing 43 tools across git, GitHub, .NET, and Ollama domains.

## Servers

| Server | File | Tools | Description |
|--------|------|-------|-------------|
| **git-tools** | `src/git_mcp.py` | 16 | Git operations (status, diff, commit, branch, push, etc.) |
| **github-tools** | `src/github_mcp.py` | 2 | GitHub utilities not in the official GitHub MCP (repo detection, workflow listing) |
| **dotnet-tools** | `src/dotnet_mcp.py` | 19 | .NET build, test, NuGet, EF migrations, code quality, coverage |
| **ollama-tools** | `src/ollama_mcp.py` | 6 | Local Ollama LLM operations (health, warmup, compression, JSON extraction) |

## Requirements

- Python 3.11+
- `mcp[cli]` - MCP SDK with CLI support
- `httpx` - HTTP client (used by ollama-tools only)

## Installation

```bash
pip install -r requirements.txt
```

Or with uv:

```bash
uv pip install -r requirements.txt
```

## Claude Code Configuration

Add to your `.claude/.mcp.json` (user-level or project-level):

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
      "args": ["G:/git/mcp-python-tools/src/ollama_mcp.py"]
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

## Design Decisions

- **stdio transport**: All servers use stdio for Claude Code compatibility
- **Cross-platform**: Windows `CREATE_NO_WINDOW` flag prevents console popups; Unix process group handling for clean timeouts
- **No bash git**: `git_mcp.py` resolves `git.exe` directly to avoid `.cmd` wrapper issues on Windows
- **English locale**: dotnet-tools forces `DOTNET_CLI_UI_LANGUAGE=en` for consistent output parsing
- **Output limits**: Large outputs (diffs, logs) are truncated to prevent context overflow
