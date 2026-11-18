# McpHub

一个 MCP 桥接器：**对上只占 Agent 的一份 MCP 配置，对下动态管理按项目绑定的子 MCP。**

解决的核心问题：Agent 只有一份全局 MCP 配置、不支持项目级 MCP，导致 sqlite、aoci 这类需要绑定项目/文件的 MCP 一切换项目就指向旧路径或直接失效。McpHub 把项目差异从"配置文件里的固定值"变成"调用时的动态参数"，切项目不需要改全局配置，也不需要重启 Agent。

设计细节见 [docs/方案设计.md](docs/方案设计.md)。

## 工作原理

```
Agent ──一份配置──> McpHub ──按 project 渲染参数──> 子 MCP 实例池
                     (元工具)                 filesystem@projA / filesystem@projB
```

- 每个子 MCP 声明 `bindings.strategy`：`global`（全项目共享一个实例）、`per_project`（每项目独立进程）、`per_file`（按文件隔离）。
- `args/env/cwd` 支持 `${PROJECT_ROOT}` `${CWD}` 等模板，调用时用 `project` 渲染。
- 实例 Key 为 `mcp:project_root`，命中复用、未命中惰性启动，空闲回收、LRU 淘汰、崩溃后重建。

## 快速开始

### 1. 安装依赖

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install mcp
```

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

`${PROJECT_ROOT}` 会渲染成调用时传入的项目目录，因此同一个 filesystem MCP 在不同项目下是独立实例，各自只能访问自己的目录。

### 3. Agent 侧只加一条

```json
{
  "mcpServers": {
    "mcp-hub": {
      "command": "D:/Projects/Python/McpHub/.venv/Scripts/python.exe",
      "args": ["-m", "mcp_hub"],
      "cwd": "D:/Projects/Python/McpHub",
      "env": { "MCPHUB_CONFIG": "C:/Users/Admin/.mcp-hub/hub.json" }
    }
  }
}
```

`cwd` 指向本项目根，保证 `-m mcp_hub` 能找到包。

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
        "/Users/me/projects/myapp"
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

## 元工具

| 工具 | 说明 |
|---|---|
| `mcp_list` | 列出子 MCP、策略、运行实例 |
| `mcp_get` | 查看单个子 MCP 配置 |
| `mcp_install` | 注册子 MCP |
| `mcp_update` | 修改配置（自动重启现有实例） |
| `mcp_remove` | 卸载并关闭实例 |
| `mcp_restart` | 重启指定/全部子 MCP |
| `mcp_set_context` | 设置默认项目 |
| `mcp_tools` | 发现子 MCP 的工具 |
| `mcp_call` | 代理执行：`mcp` + `tool` + `args` + `project` |

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
  server.py     # 对上 MCP Server（MCPServer + 9 个元工具）
  hub.py        # 配置/绑定/代理编排
  manager.py    # 实例池：惰性创建、复用、重启、LRU、空闲回收
  client.py     # stdio 子 MCP 客户端（专用 worker task 持有 session）
  bindings.py   # 模板渲染与实例 Key
  config.py     # pydantic 配置模型与项目级合并
  errors.py     # 结构化错误
config/hub.example.json
docs/方案设计.md
tests/
```
