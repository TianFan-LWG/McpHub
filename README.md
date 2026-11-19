# McpHub

> 语言：简体中文 | [English](README.en.md)

一个 MCP 桥接器：**对上只占 Agent 的一份 MCP 配置，对下动态管理按项目绑定的子 MCP。**

解决的核心问题：Agent 只有一份全局 MCP 配置、不支持项目级 MCP，导致 sqlite、aoci 这类需要绑定项目/文件的 MCP 一切换项目就指向旧路径或直接失效。McpHub 把项目差异从"配置文件里的固定值"变成"调用时的动态参数"，切项目不需要改全局配置，也不需要重启 Agent。

设计细节见 [docs/方案设计.md](docs/方案设计.md)，快速安装见 [docs/QuickStart.md](docs/QuickStart.md)。

## 工作原理

```
Agent ──一份配置──> McpHub ──按 project 渲染参数──> 子 MCP 实例池
                     (元工具)                 filesystem@projA / filesystem@projB
```

- 每个子 MCP 声明 `bindings.strategy`：`global`（全项目共享一个实例）、`per_project`（每项目独立进程）、`per_file`（按文件隔离）。
- `args/env/cwd` 支持 `${PROJECT_ROOT}` `${DB_PATH}` 等模板，调用时用 `project` 渲染。
- 实例 Key 为 `mcp:project_root`，命中复用、未命中惰性启动，空闲回收、LRU 淘汰、崩溃后重建。

## 快速开始

### 1. 安装

需要 Python 3.10+。在项目根目录从源码安装（会读取 `pyproject.toml` 并注册 `mcp-hub` 命令）：

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e .
```

也可以只装运行依赖后以模块方式启动：`.venv/Scripts/python.exe -m pip install "mcp>=1.0.0"`，再用 `python -m mcp_hub` 启动。

> **关于 `mcp` 依赖**：唯一的运行依赖是官方 mcp SDK，它只用于**对下连接子 MCP**（`client.py` 里的 `ClientSession` / `stdio_client`，调用 aoci、sqlite、filesystem 等子 MCP 时需要）。**对上**接收 Agent 请求的 MCP Server（`server.py`）是手写 JSON-RPC stdio 实现，不依赖 SDK——这样可以避免 SDK 冷启动慢导致的握手超时。所以 `mcp>=1.0.0` 不能省，否则所有子 MCP 都连不上。

### 2. 写 hub 配置

默认读取 `~/.mcp-hub/hub.json`，可用环境变量 `MCPHUB_CONFIG` 指向别处。最小示例见 [config/hub.example.json](config/hub.example.json)：

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

`${PROJECT_ROOT}` 会渲染成调用时传入的项目目录，因此同一个 filesystem MCP 在不同项目下是独立实例，各自只能访问自己的目录。`defaults` 还可配置 `idle_timeout_minutes`、`max_instances`、`call_timeout_seconds`、`start_timeout_seconds`，完整示例见 `config/hub.example.json`。

### 3. Agent 侧只加一条

推荐用仓库自带的 `stdio_launcher.py` 以**绝对路径**启动（无需 `cwd`、`env`，兼容性最好）：

```json
{
  "mcpServers": {
    "mcp-hub": {
      "type": "stdio",
      "command": "D:/dev/mcp-hub/.venv/Scripts/python.exe",
      "args": ["D:/dev/mcp-hub/stdio_launcher.py"]
    }
  }
}
```

也可以用安装好的 `mcp-hub` 脚本入口（`-m mcp_hub` 等价）：

```json
{
  "mcpServers": {
    "mcp-hub": {
      "type": "stdio",
      "command": "D:/dev/mcp-hub/.venv/Scripts/mcp-hub.exe",
      "args": []
    }
  }
}
```

以上路径均为示例，按你的实际安装位置替换。配置默认读 `~/.mcp-hub/hub.json`，如需指定其他位置再在 `env` 里加 `MCPHUB_CONFIG`（`stdio_launcher.py` 方式无需配置 `cwd`）。

> **为什么推荐绝对路径启动**：部分 Agent（如 WorkBuddy）在会话切换时会重连 MCP，并对配置里的 `cwd` 字段做规范化比对；写死 `cwd` 可能被判定为「配置变更」而触发连接重建、导致工具短暂掉线。`stdio_launcher.py` 自身会把项目根加入 `sys.path`，因此不需要 `cwd`。

## 从普通 MCP 配置迁移

普通 MCP 配置是一条扁平的 `mcpServers` 条目，路径通常写死。转成 Hub 的子 MCP 就三步：把 `command/args/env` 搬过来 → 硬编码的项目路径换成模板变量 → 选绑定策略。下面拿快速开始里的 filesystem 走一遍。

**之前**（普通配置，目录写死）：

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

**之后**（Hub 配置，目录跟着项目走）：

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

字段对照：

| 普通配置 | Hub 配置 | 说明 |
|---|---|---|
| `mcpServers.<name>` | `mcps.<name>` | 名字即 `mcp_call` 的 `mcp` 参数 |
| `command` | `command` | 原样 |
| `args` | `args` | 硬编码路径换成模板变量 |
| `env` | `env` | 值同样支持模板 |
| — | `bindings.strategy` | 新增，决定实例隔离方式 |
| — | `bindings.required` | 必需变量，缺失时直接报错而不是静默回退 |
| — | `enabled` / `description` | 可选 |

**策略怎么选：**

| 这个 MCP 的特征 | strategy |
|---|---|
| 参数含项目路径、要按项目隔离（filesystem、aoci） | `per_project` |
| 按具体库/文件隔离（sqlite） | `per_file` |
| 与项目无关、全项目共用一个实例（time、fetch） | `global` |

与项目无关的 MCP 直接原样搬，只加一个 `global`：

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

也可以不手写 JSON，用元工具装：

```
mcp_install(
  name="filesystem",
  command="npx",
  args=["-y", "@modelcontextprotocol/server-filesystem", "${PROJECT_ROOT}"],
  strategy="per_project",
  required=["PROJECT_ROOT"]
)
```

迁移时最容易踩的两个点：

1. 绝对路径要全部换成模板。只要留一处硬编码，切项目后就会指向旧目录 —— 这正是 Hub 要解决的问题。
2. `env` 里的路径同样要换，例如 aoci 的 `{"AOCI_PROJECT": "${PROJECT_ROOT}"}`。

仓库自带 [mcp-to-hub](skills/mcp-to-hub/SKILL.md) skill，能自动识别写死的路径、替换为模板变量并推断绑定策略；批量迁移时优先用它，再人工核对 `detected` 结果。

## 元工具

| 工具 | 说明 | 主要参数 |
|---|---|---|
| `mcp_list` | 列出子 MCP、策略、运行实例（含 `description`，用于判断子 MCP 能力） | — |
| `mcp_get` | 查看单个子 MCP 配置 | `name` |
| `mcp_install` | 注册子 MCP | `name`, `command`, `args?`, `env?`, `cwd?`, `strategy?`, `required?`, `db_file?`, `description?` |
| `mcp_update` | 修改配置（自动重启现有实例） | `name` + 待改字段 |
| `mcp_remove` | 卸载并关闭实例 | `name` |
| `mcp_restart` | 重启指定/全部子 MCP | `name?` |
| `mcp_set_context` | 设置默认项目 | `project?` |
| `mcp_tools` | 发现子 MCP 的工具 | `name`, `project?` |
| `mcp_call` | 代理执行 | `mcp`, `tool`, `args?`, `project?` |

### 让 Agent 主动发现子 MCP

Agent 侧只注册了 McpHub 一个入口，真实能力藏在子 MCP 里。仓库自带 [mcp-hub-discover](skills/mcp-hub-discover/SKILL.md) skill：当 Agent 在现有工具里找不到能完成任务的工具时，它先调 `mcp_list()`，读每个子 MCP 的 `description` 判断能力，再用 `mcp_tools` 发现具体工具、用 `mcp_call` 执行，而不是直接回复"做不到"。因此**建议给子 MCP 都填上 `description`**——它直接决定 Agent 能否找到这个工具。

典型调用：

```json
{ "mcp": "filesystem", "tool": "list_directory", "args": { "path": "src" }, "project": "D:/work/proj-a" }
```

返回：

```json
{ "ok": true, "mcp": "filesystem", "instance": "filesystem:d:/work/proj-a", "is_error": false, "content": [{ "type": "text", "text": "[DIR] components; [FILE] main.ts" }] }
```

## 项目级覆盖

在项目根放 `.mcp-hub.json`，只写差异：

```json
{
  "mcps": {
    "filesystem": {
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "${PROJECT_ROOT}", "${PROJECT_ROOT}/../shared"]
    }
  }
}
```

这个项目额外允许访问同级的 `shared` 目录。切换项目时 McpHub 自动加载并深度合并到全局配置之上，全局配置不用动。

## 日志

McpHub 会记录子 MCP 的完整生命周期（实例启动/就绪/崩溃/回收/淘汰）、工具调用与报错，便于排查"子 MCP 没起来""工具调用失败"等问题。

- **默认路径**：`~/.mcp-hub/logs/mcp-hub.log`，每天午夜轮转，保留 14 天（如 `mcp-hub.log.2026-11-20`）。
- **绝不写 stdout**：日志只写文件或可选地回显 stderr，不会污染 stdio MCP 协议。默认不回显 stderr。

日志配置写在 `hub.json` 的 `defaults.logging`：

```json
{
  "defaults": {
    "logging": {
      "level": "INFO",
      "dir": null,
      "stderr": false,
      "backup_days": 14
    }
  }
}
```

| 字段 | 说明 | 默认 |
|---|---|---|
| `level` | `DEBUG` / `INFO` / `WARNING` / `ERROR`；`DEBUG` 会额外记录每一条收发的协议帧 | `INFO` |
| `dir` | 日志目录；`null` 表示 `~/.mcp-hub/logs` | `null` |
| `stderr` | 是否同时把日志写到 stderr | `false` |
| `backup_days` | 按天轮转保留天数 | `14` |

日志路径也能像配置文件路径（`MCPHUB_CONFIG`）一样用环境变量指定，且**环境变量优先级高于 hub.json**：

| 环境变量 | 说明 | 示例 |
|---|---|---|
| `MCPHUB_LOG` | 日志文件完整路径，优先级最高（与 `MCPHUB_CONFIG` 对称） | `D:/logs/mcp-hub.log` |
| `MCPHUB_LOG_DIR` | 日志目录，文件名固定为 `mcp-hub.log` | `D:/logs` |
| `MCPHUB_LOG_LEVEL` | 覆盖 `defaults.logging.level` | `DEBUG` |
| `MCPHUB_LOG_STDERR` | `1` 表示同时回显到 stderr | `1` |

优先级：`MCPHUB_LOG` > `MCPHUB_LOG_DIR` > `defaults.logging.dir` > 默认 `~/.mcp-hub/logs/mcp-hub.log`。例如设置 `MCPHUB_LOG=D:/logs/my-hub.log` 后，日志就固定写到该文件。

典型日志片段：

```
2026-11-20 03:53:52.660 INFO    [25896] 客户端握手：协议版本=2025-11-25
2026-11-20 03:53:52.670 INFO    [25896] Hub 初始化：配置=...\hub.json 子MCP数=1 日志目录=...
2026-11-20 03:53:52.671 INFO    [25896] stdin 关闭，开始关停
2026-11-20 03:53:52.671 INFO    [25896] McpHub 优雅关停完成
```

排查子 MCP 问题时，把 `level` 调到 `DEBUG` 重启即可看到完整的协议交互与实例状态变化。注：早期版本的 `~/.mcp-hub/stdio-debug.log` 已被本日志系统取代，不再生成。

## 模板变量

| 变量 | 含义 |
|---|---|
| `${PROJECT_ROOT}` | 项目根目录绝对路径 |
| `${PROJECT_NAME}` | 项目目录名 |
| `${PROJECT_ID}` | 项目路径短 hash |
| `${CWD}` | 调用方 cwd（默认等于项目根） |
| `${HOME}` | 用户主目录 |
| `${DB_PATH}` | 由 `bindings.db_file` 解析出的数据库绝对路径（数据库类 MCP 用，见文末附注） |
| `${ENV:NAME}` | 读取环境变量 |

模板需要项目但调用未传 `project`、也没有默认项目时，直接返回 `PROJECT_REQUIRED` —— 不会静默回退到旧路径。

## 测试

```bash
.venv/Scripts/python.exe -m unittest discover -s tests -v
```

- `test_bindings.py`：模板渲染、实例 Key、项目隔离、overlay 合并（9 个用例）。
- `test_e2e_sqlite.py`：MCP 协议层连通性 + 真实启动子 MCP 验证两个项目命中不同数据库（用 sqlite 覆盖 per_project 隔离）。

## 目录

```
mcp_hub/
  __init__.py   # 包导出
  __main__.py   # python -m mcp_hub 入口
  server.py     # 对上 MCP Server（手写 JSON-RPC stdio + 9 个元工具）
  hub.py        # 配置/绑定/代理编排
  manager.py    # 实例池：惰性创建、复用、重启、LRU、空闲回收
  client.py     # stdio 子 MCP 客户端（专用 worker task 持有 session）
  logger.py     # 日志系统：按天轮转、stdout 零污染、可配置级别/路径
  bindings.py   # 模板渲染与实例 Key
  config.py     # pydantic 配置模型与项目级合并（含 defaults.logging）
  errors.py     # 结构化错误
stdio_launcher.py         # 绝对路径 stdio 启动入口（推荐给 Agent 使用）
config/hub.example.json   # 完整配置示例
docs/
  方案设计.md             # 设计细节
  QuickStart.md           # 快速安装指引
skills/
  mcp-to-hub/              # 普通 MCP 配置 -> Hub 子 MCP 的转换 skill
    SKILL.md
    scripts/convert.py
    references/conversion-rules.md
  mcp-hub-discover/        # 引导 Agent 工具找不到时先查 Hub 子 MCP
    SKILL.md
tests/
  test_bindings.py
  test_e2e_sqlite.py
pyproject.toml            # 包元数据与 mcp-hub 命令入口
```

运行时生成（不入库）：日志默认在 `~/.mcp-hub/logs/`，配置在 `~/.mcp-hub/hub.json`。

## 附注

`${DB_PATH}` 由 `bindings.db_file` 解析：相对路径基于当前 `project` 目录，绝对路径原样使用；`db_file` 属于绑定配置，可在项目级 `.mcp-hub.json` 里针对不同项目指定各自的库文件。
