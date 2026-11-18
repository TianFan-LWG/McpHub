# 转换规则与示例

## 字段映射

| 普通配置 | Hub 配置 | 处理方式 |
|---|---|---|
| `mcpServers.<name>` | `mcps.<name>` | key 即 `mcp_call` 的 `mcp` 参数 |
| `command` | `command` | 原样保留 |
| `args` | `args` | 绝对路径替换为模板 |
| `env` | `env` | 值中的绝对路径同样替换 |
| `cwd` | `cwd` | 绝对路径替换为 `${PROJECT_ROOT}` |
| — | `bindings.strategy` | 按下方决策树推断 |
| — | `bindings.required` | 由用到的模板变量推导 |
| — | `bindings.db_file` | 仅数据库类且 `per_project` 时生成 |
| — | `enabled` / `description` | 可选透传 |

`type` 固定为 `stdio`，普通配置里的 `type` 字段会被忽略（Hub v1 只支持 stdio）。

## 策略决策树

```
存在绝对路径？
├─ 否 → global（所有项目共用一个实例）
└─ 是
   ├─ 有 .db/.sqlite/.sqlite3 路径
   │  ├─ 传了 --project 且路径在项目内 → per_project + db_file
   │  └─ 否则 → per_file（实例 Key 按文件路径）
   └─ 只有目录路径 → per_project
```

模板变量与 required 的对应：

| 用到的模板 | required |
|---|---|
| `${PROJECT_ROOT}` | `["PROJECT_ROOT"]` |
| `${DB_PATH}` | `["DB_PATH"]`（per_project 时再加 `PROJECT_ROOT`） |
| 无 | `[]` |

## 示例一：目录绑定（filesystem）

输入：

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/Users/me/projects/myapp"]
    }
  }
}
```

输出：

```json
{
  "name": "filesystem",
  "type": "stdio",
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-filesystem", "${PROJECT_ROOT}"],
  "env": {},
  "enabled": true,
  "bindings": { "strategy": "per_project", "required": ["PROJECT_ROOT"] }
}
```

## 示例二：数据库绑定（sqlite，带 --project）

输入：

```json
{
  "mcpServers": {
    "db": {
      "command": "uvx",
      "args": ["--with", "mcp<2", "mcp-server-sqlite", "--db-path", "/Users/me/projects/myapp/data/app.db"]
    }
  }
}
```

命令：`convert.py config.json --project /Users/me/projects/myapp`

输出：

```json
{
  "name": "db",
  "type": "stdio",
  "command": "uvx",
  "args": ["--with", "mcp<2", "mcp-server-sqlite", "--db-path", "${DB_PATH}"],
  "env": {},
  "enabled": true,
  "bindings": {
    "strategy": "per_project",
    "required": ["PROJECT_ROOT", "DB_PATH"],
    "db_file": "data/app.db"
  }
}
```

不传 `--project` 时结果变成 `{"strategy": "per_file", "required": ["DB_PATH"]}`，并给出一条 warning。

## 示例三：env 绑定的 MCP（aoci 类）

输入：

```json
{
  "mcpServers": {
    "aoci": {
      "command": "aoci-mcp",
      "args": [],
      "env": { "AOCI_PROJECT": "/Users/me/projects/myapp" }
    }
  }
}
```

输出：

```json
{
  "name": "aoci",
  "type": "stdio",
  "command": "aoci-mcp",
  "args": [],
  "env": { "AOCI_PROJECT": "${PROJECT_ROOT}" },
  "enabled": true,
  "bindings": { "strategy": "per_project", "required": ["PROJECT_ROOT"] }
}
```

## 示例四：无路径（global）

输入：

```json
{
  "mcpServers": {
    "time": { "command": "uvx", "args": ["mcp-server-time"] }
  }
}
```

输出 `bindings: {"strategy": "global"}`，`args` 不变，所有项目共用一个实例。

## 实例 Key 规则

转换结果不会写死实例 Key，它在运行时生成：

| strategy | instance_key |
|---|---|
| `global` | `<name>:__global__` |
| `per_project` | `<name>:<规范化后的项目根>` |
| `per_file` | `<name>:<规范化后的目标文件>` |

因此两个项目调用同一个 `per_project` 条目时，会得到两个独立进程，互不影响。
