# Contributing to MCP Dev Servers

Thanks for your interest in contributing! Here's how to get started.

## Prerequisites

- Python 3.11+
- The external tool for the server you're working on (see README for per-server requirements)

## Development Setup

1. Fork and clone the repository
2. Create a virtual environment and install dependencies:

```bash
python -m venv .venv
.venv/Scripts/activate      # Windows
# source .venv/bin/activate  # Linux/macOS
pip install -r requirements.txt
```

3. Register the server you're working on with Claude Code (see README for `claude mcp add` commands)

## Project Structure

| File | Server | Tools |
|------|--------|-------|
| `src/git_mcp.py` | git-tools | 16 |
| `src/github_mcp.py` | github-tools | 2 |
| `src/dotnet_mcp.py` | dotnet-tools | 19 |
| `src/ollama_mcp.py` | ollama-tools | 6 |
| `src/rust_mcp.py` | rust-tools | 4 |

Each server is a standalone Python file using the FastMCP framework. There are no shared modules between servers.

## Adding a New Server

1. Create `src/<name>_mcp.py` following the pattern of existing servers
2. Use `FastMCP("<name>-tools")` for the server name
3. Add the server to `README.md` (servers table, prerequisites, registration commands, tool reference, JSON config)
4. Test locally with `claude mcp add` before submitting

## Making Changes

1. Create a feature branch from `main`
2. Make your changes
3. Test by registering the modified server with Claude Code and exercising the tools
4. Commit with a clear message describing what and why
5. Open a pull request

## Pull Request Guidelines

- Keep PRs focused -- one concern per PR
- Describe what changed and why in the PR description
- Test the affected MCP server end-to-end with Claude Code
- Maintain cross-platform compatibility (Windows + Linux/macOS)

## Design Principles

- **stdio transport**: All servers use stdio for Claude Code compatibility
- **Cross-platform**: Handle Windows and Unix differences (process groups, path resolution)
- **Output limits**: Truncate large outputs to prevent context overflow
- **No shared state**: Each server is fully self-contained

## Reporting Issues

- Use [GitHub Issues](https://github.com/dagonet/mcp-dev-servers/issues) for bug reports and feature requests
- Include the server name, Claude Code version, and OS in bug reports
- Check existing issues before opening a new one
