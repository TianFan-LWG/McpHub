---
name: mcp-hub-discover
description: 当 Agent 在当前工具集里找不到能完成任务的工具时，先通过 McpHub 的 mcp_list 查看已安装的子 MCP，借助每个子 MCP 的 description 判断能力，再用 mcp_tools 发现具体工具、用 mcp_call 执行。适用于"没有这个工具""找不到工具""有没有工具能 X""这件事该用哪个工具""MCP 里有没有 X"这类场景。触发词包括找不到工具、没有对应工具、缺少某能力、有哪些 MCP、MCP 工具、mcp_list、子 MCP、Hub 里有没有。
agent_created: true
---

# 先用 McpHub 找工具

Agent 只注册了 McpHub 一个 MCP，真实能力藏在它管理的子 MCP 里。当你扫一遍手上工具、发现没有能完成当前任务的工具时，**不要直接说"我做不到"**——先按下面的流程去 Hub 里找一圈。

## 何时使用

- 当前工具集里没有能完成任务的工具。
- 用户问"有没有能 X 的工具""哪个工具能做 Y"。
- 想确认某个能力是否已经以 MCP 形式接进来。
- 需要操作某个项目/数据库，但没看到对应的专用工具。

## 流程

**1. 列出所有子 MCP**

调用 `mcp_list()`，返回里每个条目都有：

| 字段 | 用途 |
|---|---|
| `name` | `mcp_tools` / `mcp_call` 里的 `mcp` 参数 |
| `description` | **最重要**：这段 MCP 能做什么，用来判断它是否覆盖当前需求 |
| `strategy` | `global` 不需要 project；`per_project` / `per_file` 需要 |
| `enabled` | 只考虑 `true` 的条目 |
| `instances` / `instance_keys` | 已在运行的实例，辅助判断 |

返回顶层还有 `active_project`，它就是默认 project 上下文（来自 `mcp_set_context`） 。

**2. 用 description 筛出候选**

逐个读 `description`，挑出和当前需求相关的子 MCP。description 为空或含糊时，用 `mcp_get(name=...)` 看完整配置（command、args、env）再判断。

**3. 发现具体工具**

对候选调用 `mcp_tools(name=..., project=...)`，拿到工具名、description 和 input_schema，确认其中有能用的工具。

- `per_project` / `per_file` 必须传 `project`，否则返回 `PROJECT_REQUIRED`。
- `global` 可以省略 project。
- 不知道 project 路径时，用 `mcp_list` 返回的 `active_project`；仍为空就向用户确认项目目录。

**4. 代理执行**

用 `mcp_call(mcp=..., tool=..., args=..., project=...)` 调用。返回结果里 `is_error` 为 `true` 表示子 MCP 报错，如实转述 `content`。

**5. 找不到就明确告知**

若所有子 MCP 的 description 和工具都不匹配，再告诉用户当前没有这个能力，并说明你已经查过 Hub。

## 例子

用户要"看看 proj-a 里有多少条记录"，但手上没有数据库工具：

```
mcp_list()
  -> mcps: [{name: "sqlite", description: "Query project-local SQLite databases.",
             strategy: "per_project", enabled: true}, ...]

mcp_tools(name="sqlite", project="D:/work/proj-a")
  -> tools: [{name: "read_query", ...}, ...]

mcp_call(mcp="sqlite", tool="read_query",
         args={"query": "select count(*) as n from notes"},
         project="D:/work/proj-a")
```

## 常见坑

- **跳过 mcp_list 直接猜**：不要凭名字猜有什么工具，先列再查，description 是判断依据。
- **漏传 project**：`per_project` / `per_file` 的子 MCP 不传 project 会报 `PROJECT_REQUIRED`，不是"工具不存在"。
- **忽略 enabled**：`enabled=false` 的条目不会被 `mcp_call` 接受。
- **把 Hub 自身的元工具当业务工具**：`mcp_list` / `mcp_tools` 等只是管理面，真正干活的是子 MCP 的工具，要用 `mcp_call` 代理执行。
- **description 缺失就放弃**：description 为空不代表没能力，用 `mcp_get` 看配置或 `mcp_tools` 直接列工具。
