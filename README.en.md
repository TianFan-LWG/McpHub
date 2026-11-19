# McpHub

> Language: English | [简体中文](README.md)

An MCP bridge: **one MCP entry for the Agent, with child MCPs dynamically managed per project behind it.**

The problem it solves: an Agent has only a single global MCP config and no project-level MCP support, so MCPs that must bind to a project or file (sqlite, aoci, etc.) keep pointing at stale paths or break when you switch projects. McpHub turns project differences from "fixed values in a config file" into "dynamic arguments at call time", so switching projects needs no config edits and no Agent restart.

Design details: [docs/方案设计.md](docs/方案设计.md). Quick install: [docs/QuickStart.md](docs/QuickStart.md).

## How it works

```
Agent ──one config──> McpHub ──render args by project──> child MCP instance pool
                      (meta tools)               filesystem@projA / filesystem@projB
```

- Each child MCP declares `bindings.strategy`: `global` (one shared instance), `per_project` (a separate process per project), or `per_file` (isolated by file).
- `args/env/cwd` support templates such as `${PROJECT_ROOT}` and `${DB_PATH}`, rendered at call time from `project`.
- The instance key is `mcp:project_root`; hits are reused, misses start lazily, with idle reaping, LRU eviction, and rebuild after a crash.

## Quick start

### 1. Install

Requires Python 3.10+. From the project root, install from source (this reads `pyproject.toml` and registers the `mcp-hub` command):

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e .
```

Alternatively, install only the runtime dependency with `.venv/Scripts/python.exe -m pip install "mcp>=1.0.0"` and start it as a module with `python -m mcp_hub`.

### 2. Write the hub config

By default it reads `~/.mcp-hub/hub.json`; set `MCPHUB_CONFIG` to point elsewhere. See [config/hub.example.json](config/hub.example.json) for a minimal example:

```json
{
  "version": 1,
  "defaults": { "default_project": null },
  "mcps": {
    "filesystem": {
      "name": "filesystem",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "${PROJECT_ROOT}"],
      "bindings": {
        "strategy": "per_project",
        "required": ["PROJECT_ROOT"]
      }
    }
  }
}
```

`${PROJECT_ROOT}` renders to the project directory passed at call time, so the same filesystem MCP becomes an independent instance per project, each confined to its own directory. `defaults` also accepts `idle_timeout_minutes`, `max_instances`, `call_timeout_seconds`, and `start_timeout_seconds`; see `config/hub.example.json` for the full example.

### 3. Add a single entry on the Agent side

Using the installed `mcp-hub` script entry point:

```json
{
  "mcpServers": {
    "mcp-hub": {
      "command": "D:/dev/mcp-hub/.venv/Scripts/mcp-hub.exe",
      "args": [],
      "env": { "MCPHUB_CONFIG": "C:/Users/example/.mcp-hub/hub.json" }
    }
  }
}
```

Or start it as a module using the virtual environment's Python:

```json
{
  "mcpServers": {
    "mcp-hub": {
      "command": "D:/dev/mcp-hub/.venv/Scripts/python.exe",
      "args": ["-m", "mcp_hub"],
      "cwd": "D:/dev/mcp-hub",
      "env": { "MCPHUB_CONFIG": "C:/Users/example/.mcp-hub/hub.json" }
    }
  }
}
```

All paths above are illustrative — replace them with your actual install locations. In module mode, `cwd` points to the project root so that `-m mcp_hub` can find the package.

## Migrating from a plain MCP config

A plain MCP config is a flat `mcpServers` entry with hard-coded paths. Converting it to a Hub child MCP takes three steps: copy `command/args/env` over → replace hard-coded project paths with template variables → pick a binding strategy. Here is the filesystem example from Quick start.

**Before** (plain config, directory hard-coded):

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": [
        "-y",
        "@modelcontextprotocol/server-filesystem",
        "D:/work/myapp"
      ]
    }
  }
}
```

**After** (Hub config, directory follows the project):

```json
{
  "mcps": {
    "filesystem": {
      "name": "filesystem",
      "command": "npx",
      "args": [
        "-y",
        "@modelcontextprotocol/server-filesystem",
        "${PROJECT_ROOT}"
      ],
      "bindings": {
        "strategy": "per_project",
        "required": ["PROJECT_ROOT"]
      }
    }
  }
}
```

Field mapping:

| Plain config | Hub config | Notes |
|---|---|---|
| `mcpServers.<name>` | `mcps.<name>` | The name is the `mcp` argument of `mcp_call` |
| `command` | `command` | Unchanged |
| `args` | `args` | Replace hard-coded paths with templates |
| `env` | `env` | Values support templates too |
| — | `bindings.strategy` | New; decides instance isolation |
| — | `bindings.required` | Required variables; missing ones error out instead of silently falling back |
| — | `enabled` / `description` | Optional |

**Choosing a strategy:**

| Characteristic of the MCP | strategy |
|---|---|
| Contains a project path, needs per-project isolation (filesystem, aoci) | `per_project` |
| Isolated by a specific database/file (sqlite) | `per_file` |
| Project-independent, one shared instance (time, fetch) | `global` |

For a project-independent MCP, copy it as-is and just add `global`:

```json
{
  "mcps": {
    "time": {
      "name": "time",
      "command": "uvx",
      "args": ["mcp-server-time"],
      "bindings": { "strategy": "global" }
    }
  }
}
```

You can also skip hand-writing JSON and use the meta tool:

```
mcp_install(
  name="filesystem",
  command="npx",
  args=["-y", "@modelcontextprotocol/server-filesystem", "${PROJECT_ROOT}"],
  strategy="per_project",
  required=["PROJECT_ROOT"]
)
```

Two easy mistakes when migrating:

1. Replace every absolute path with a template. Leave even one hard-coded path and it will point at the old directory after a project switch — exactly the problem Hub exists to fix.
2. Paths in `env` need replacing too, e.g. aoci's `{"AOCI_PROJECT": "${PROJECT_ROOT}"}`.

The repo ships an [mcp-to-hub](skills/mcp-to-hub/SKILL.md) skill that detects hard-coded paths, substitutes templates, and infers binding strategies automatically. Prefer it for bulk migrations, then review the `detected` results manually.

## Meta tools

| Tool | Description | Main parameters |
|---|---|---|
| `mcp_list` | List child MCPs, strategies, running instances (includes `description` for capability discovery) | — |
| `mcp_get` | Show one child MCP's config | `name` |
| `mcp_install` | Register a child MCP | `name`, `command`, `args?`, `env?`, `cwd?`, `strategy?`, `required?`, `db_file?`, `description?` |
| `mcp_update` | Modify config (restarts existing instances) | `name` + fields to change |
| `mcp_remove` | Uninstall and close instances | `name` |
| `mcp_restart` | Restart one or all child MCPs | `name?` |
| `mcp_set_context` | Set the default project | `project?` |
| `mcp_tools` | Discover a child MCP's tools | `name`, `project?` |
| `mcp_call` | Proxy a call | `mcp`, `tool`, `args?`, `project?` |

### Helping Agents discover child MCPs

The Agent registers only McpHub as an entry; the real capabilities live in child MCPs. The repo ships an [mcp-hub-discover](skills/mcp-hub-discover/SKILL.md) skill: when the Agent cannot find a tool for the task, it calls `mcp_list()`, reads each child MCP's `description` to judge capabilities, then uses `mcp_tools` to discover concrete tools and `mcp_call` to run them — instead of replying "I can't". So **give every child MCP a `description`**: it directly determines whether the Agent can find that tool.

Typical call:

```json
{ "mcp": "filesystem", "tool": "list_directory", "args": { "path": "src" }, "project": "D:/work/proj-a" }
```

Response:

```json
{ "ok": true, "mcp": "filesystem", "instance": "filesystem:d:/work/proj-a", "is_error": false, "content": [{ "type": "text", "text": "[DIR] components; [FILE] main.ts" }] }
```

## Project-level overrides

Place a `.mcp-hub.json` in the project root with only the differences:

```json
{
  "mcps": {
    "filesystem": {
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "${PROJECT_ROOT}", "${PROJECT_ROOT}/../shared"]
    }
  }
}
```

This project additionally allows access to the sibling `shared` directory. When switching projects, McpHub automatically loads and deep-merges it over the global config, leaving the global config untouched.

## Template variables

| Variable | Meaning |
|---|---|
| `${PROJECT_ROOT}` | Absolute path of the project root |
| `${PROJECT_NAME}` | Project directory name |
| `${PROJECT_ID}` | Short hash of the project path |
| `${CWD}` | Caller's cwd (defaults to the project root) |
| `${HOME}` | User home directory |
| `${DB_PATH}` | Absolute database path resolved from `bindings.db_file` (for database MCPs; see Notes) |
| `${ENV:NAME}` | Read an environment variable |

When a template needs a project but no `project` was passed and no default is set, `PROJECT_REQUIRED` is returned — no silent fallback to a stale path.

## Tests

```bash
.venv/Scripts/python.exe -m unittest discover -s tests -v
```

- `test_bindings.py`: template rendering, instance keys, project isolation, overlay merging (9 cases).
- `test_e2e_sqlite.py`: MCP protocol connectivity plus a real child MCP verifying two projects hit different databases (sqlite covering per_project isolation).

## Layout

```
mcp_hub/
  __init__.py   # package exports
  __main__.py   # python -m mcp_hub entry point
  server.py     # upstream MCP Server (MCPServer + 9 meta tools)
  hub.py        # config / binding / proxy orchestration
  manager.py    # instance pool: lazy create, reuse, restart, LRU, idle reaping
  client.py     # stdio child MCP client (dedicated worker task holds the session)
  bindings.py   # template rendering and instance keys
  config.py     # pydantic config models and project-level merging
  errors.py     # structured errors
config/hub.example.json   # full config example
docs/
  方案设计.md             # design details
  QuickStart.md           # quick install guide
skills/
  mcp-to-hub/              # skill: plain MCP config -> Hub child MCP
    SKILL.md
    scripts/convert.py
    references/conversion-rules.md
  mcp-hub-discover/        # skill: check Hub child MCPs when a tool is missing
    SKILL.md
tests/
  test_bindings.py
  test_e2e_sqlite.py
pyproject.toml            # package metadata and the mcp-hub command entry point
```

## Notes

`${DB_PATH}` is resolved from `bindings.db_file`: relative paths are based on the current `project`, absolute paths are used as-is. `db_file` is part of the binding config, so a project-level `.mcp-hub.json` can point each project at its own database file.
