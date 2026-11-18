from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from .errors import HubError
from .hub import Hub

mcp = MCPServer("mcp-hub")
_hub: Hub | None = None


def get_hub() -> Hub:
    global _hub
    if _hub is None:
        _hub = Hub()
    return _hub


def _fail(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, HubError):
        return exc.to_dict()
    return {"ok": False, "error": {"code": "INTERNAL", "message": str(exc) or exc.__class__.__name__}}


@mcp.tool()
async def mcp_list() -> dict[str, Any]:
    """列出已安装的子 MCP、绑定策略与当前运行实例。"""
    try:
        return get_hub().list()
    except Exception as exc:
        return _fail(exc)


@mcp.tool()
async def mcp_get(name: str) -> dict[str, Any]:
    """查看某个子 MCP 的完整配置与实例 Key。"""
    try:
        return get_hub().get(name)
    except Exception as exc:
        return _fail(exc)


@mcp.tool()
async def mcp_install(
    name: str,
    command: str,
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    strategy: str = "per_project",
    required: list[str] | None = None,
    db_file: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """安装（注册）一个子 MCP。strategy 可选 global / per_project / per_file；args/env/cwd 支持 ${PROJECT_ROOT} ${DB_PATH} 等模板。"""
    try:
        return get_hub().install(
            name=name,
            command=command,
            args=args,
            env=env,
            cwd=cwd,
            strategy=strategy,
            required=required,
            db_file=db_file,
            description=description,
        )
    except Exception as exc:
        return _fail(exc)


@mcp.tool()
async def mcp_update(
    name: str,
    command: str | None = None,
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    strategy: str | None = None,
    required: list[str] | None = None,
    db_file: str | None = None,
    enabled: bool | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """修改子 MCP 配置；改动会自动重启该 MCP 的现有实例。"""
    changes = {
        "command": command,
        "args": args,
        "env": env,
        "cwd": cwd,
        "strategy": strategy,
        "required": required,
        "db_file": db_file,
        "enabled": enabled,
        "description": description,
    }
    try:
        return await get_hub().update(name, {key: value for key, value in changes.items() if value is not None})
    except Exception as exc:
        return _fail(exc)


@mcp.tool()
async def mcp_remove(name: str) -> dict[str, Any]:
    """卸载子 MCP 并关闭它的全部实例。"""
    try:
        return await get_hub().remove(name)
    except Exception as exc:
        return _fail(exc)


@mcp.tool()
async def mcp_restart(name: str | None = None) -> dict[str, Any]:
    """重启指定子 MCP（不传 name 则重启全部），下次调用时按最新配置惰性重建。"""
    try:
        return await get_hub().restart(name)
    except Exception as exc:
        return _fail(exc)


@mcp.tool()
async def mcp_set_context(project: str | None = None) -> dict[str, Any]:
    """设置默认项目上下文；后续 mcp_call 未显式传 project 时使用它。传空值则清除。"""
    try:
        return get_hub().set_context(project)
    except Exception as exc:
        return _fail(exc)


@mcp.tool()
async def mcp_tools(name: str, project: str | None = None) -> dict[str, Any]:
    """发现某个子 MCP 暴露的工具。会按需启动该项目的实例。"""
    try:
        return await get_hub().tools(name, project)
    except Exception as exc:
        return _fail(exc)


@mcp.tool()
async def mcp_call(
    mcp: str,
    tool: str,
    args: dict[str, Any] | None = None,
    project: str | None = None,
) -> dict[str, Any]:
    """代理执行子 MCP 的工具。project 决定绑定到哪个项目（sqlite 会指向该项目的库文件）。"""
    try:
        return await get_hub().call(mcp, tool, args, project)
    except Exception as exc:
        return _fail(exc)


def main() -> None:
    mcp.run()
