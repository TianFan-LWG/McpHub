---
name: mcp-to-hub
description: 把普通 MCP 配置（mcpServers 里的 command/args/env）转换成 McpHub 的子 MCP 配置，自动识别写死的路径并替换为 ${PROJECT_ROOT} 或 ${DB_PATH}，推断 global/per_project/per_file 绑定策略。本技能适用于迁移 MCP、把某个 MCP 加入 Hub、把硬编码路径改成按项目绑定的场景；触发词包括转换 MCP 配置、迁移 MCP、mcpServers 转 hub、把 X MCP 加到 Hub。
agent_created: true
---

# MCP 配置转 Hub 子 MCP

把普通 MCP 配置转换成 McpHub 的 `mcps` 条目。核心不是搬字段，而是识别写死的路径、替换成模板变量、选对绑定策略，让同一个 MCP 在不同项目下自动指向各自的目录或数据库。

## 何时使用

- 用户提供了一份 `mcpServers` 配置，要接进 McpHub。
- 用户要求把某个 MCP "加到 Hub"、"迁移进来"、"让它跟着项目走"。
- 已有 Hub 条目里写死了路径，需要改成 `${PROJECT_ROOT}` / `${DB_PATH}`。

## 输入形态

脚本接受三种 JSON：

1. `{"mcpServers": {"filesystem": {...}}}` —— Claude Desktop / VS Code 常见格式
2. `{"filesystem": {...}}` —— 只有服务器映射
3. `{"command": "...", "args": [...]}` —— 单个服务器，配合 `--name`

## 流程

1. 取得源配置。若是用户粘贴的内容，写入系统临时目录（Windows 用 `%TEMP%`），不要落在项目根。
2. 预览转换结果：
   ```bash
   python scripts/convert.py <config.json> --project <project-root>
   ```
   不传 `--project` 也能转换，但数据库类会被判成 `per_file`。
3. 检查 `detected` 每一条：被替换的路径是否确实代表项目根或目标数据库。
4. 确认后合并到 Hub 配置：
   ```bash
   python scripts/convert.py <config.json> --project <project-root> --write ~/.mcp-hub/hub.json
   ```
   已存在同名条目时需加 `--force`。
5. 验证：
   - `mcp_list` 能看到新条目与策略
   - `mcp_tools(name, project)` 能列出工具
   - `mcp_call(name, tool, args, project)` 跑一次真实调用

## 脚本的判断规则

- 扫描 `args`、`env`、`cwd`，识别绝对路径（Windows 盘符 / UNC，POSIX 以 `/` 开头）。
- 以 `.db` / `.sqlite` / `.sqlite3` 结尾 → `${DB_PATH}`；其余绝对路径 → `${PROJECT_ROOT}`。
- 出现数据库路径 → `per_project` + `db_file`（传了 `--project` 才会算相对路径），否则退回 `per_file`。
- 只出现目录路径 → `per_project` + `required: ["PROJECT_ROOT"]`。
- 完全没有路径 → `global`。
- 需要人工判断的点写入 warnings。

字段映射、策略决策树和完整示例见 `references/conversion-rules.md`。

## 必须人工确认

1. 替换成 `${PROJECT_ROOT}` 的路径，是项目根，还是所有项目共用的固定资源目录？后者应保留原值或改用 `global`。
2. 同一 MCP 同时出现项目目录和数据库路径时，确认是否两者都需要绑定。
3. `env` 里的路径是否也随项目变化。脚本会替换，但语义要人来判断。
4. 目标包是否仍然存在。脚本只转换配置，不检查 npm/PyPI 上的包；下架的包转换后依然启动失败。

## 常见坑

- 只改 `args` 漏掉 `env`：脚本已覆盖，手工改写时最容易漏。
- 把共享目录误当项目根：每个项目都会起独立实例却指向同一目录，白占进程。
- 不传 `--project` 导致数据库被判 `per_file`：实例 Key 变成文件路径，跨项目不按项目聚合。
- Windows 路径含反斜杠：JSON 中写 `\\`，读入后是单反斜杠，模板替换不受影响。
- `npx` 与 `uvx` 参数风格不同：位置参数和 `--db-path` 不能互套，转换时保持原样，不要擅自改。
