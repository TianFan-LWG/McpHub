from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import traceback
from typing import Any

from . import logger
from .errors import HubError
from .hub import Hub

SERVER_INFO = {"name": "mcp-hub", "version": "1.0.0"}
PROTOCOL_VERSION = "2024-11-05"

log = logger.get_logger()  # 懒配置：Hub 初始化后会按 hub.json 重新 configure

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


async def mcp_list() -> dict[str, Any]:
    """List installed child MCPs with their binding strategies and currently running instances."""
    try:
        return get_hub().list()
    except Exception as exc:
        return _fail(exc)


async def mcp_get(name: str) -> dict[str, Any]:
    """Show the full configuration and instance keys of a child MCP."""
    try:
        return get_hub().get(name)
    except Exception as exc:
        return _fail(exc)


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
    """Install (register) a child MCP. strategy can be global / per_project / per_file; args/env/cwd support templates such as ${PROJECT_ROOT} and ${DB_PATH}."""
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
    """Modify a child MCP's configuration; changes automatically restart that MCP's existing instances."""
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


async def mcp_remove(name: str) -> dict[str, Any]:
    """Uninstall a child MCP and close all of its instances."""
    try:
        return await get_hub().remove(name)
    except Exception as exc:
        return _fail(exc)


async def mcp_restart(name: str | None = None) -> dict[str, Any]:
    """Restart a child MCP (all if name is omitted); instances are lazily rebuilt with the latest configuration on next use."""
    try:
        return await get_hub().restart(name)
    except Exception as exc:
        return _fail(exc)


async def mcp_set_context(project: str | None = None) -> dict[str, Any]:
    """Set the default project context used by mcp_call when project is not explicitly passed; pass an empty value to clear it."""
    try:
        return get_hub().set_context(project)
    except Exception as exc:
        return _fail(exc)


async def mcp_tools(name: str, project: str | None = None) -> dict[str, Any]:
    """Discover the tools exposed by a child MCP, starting the instance for that project on demand."""
    try:
        return await get_hub().tools(name, project)
    except Exception as exc:
        return _fail(exc)


async def mcp_call(
    mcp: str,
    tool: str,
    args: dict[str, Any] | None = None,
    project: str | None = None,
) -> dict[str, Any]:
    """Proxy a tool call to a child MCP. project determines which project to bind to (sqlite points to that project's database file)."""
    try:
        return await get_hub().call(mcp, tool, args, project)
    except Exception as exc:
        return _fail(exc)


def _schema(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}


TOOLS: list[dict[str, Any]] = [
    {"name": "mcp_list", "description": mcp_list.__doc__ or "", "inputSchema": _schema({})},
    {"name": "mcp_get", "description": mcp_get.__doc__ or "", "inputSchema": _schema({"name": {"type": "string"}}, ["name"])},
    {
        "name": "mcp_install",
        "description": mcp_install.__doc__ or "",
        "inputSchema": _schema(
            {
                "name": {"type": "string"},
                "command": {"type": "string"},
                "args": {"type": "array", "items": {"type": "string"}},
                "env": {"type": "object", "additionalProperties": {"type": "string"}},
                "cwd": {"type": "string"},
                "strategy": {"type": "string", "enum": ["global", "per_project", "per_file"]},
                "required": {"type": "array", "items": {"type": "string"}},
                "db_file": {"type": "string"},
                "description": {"type": "string"},
            },
            ["name", "command"],
        ),
    },
    {
        "name": "mcp_update",
        "description": mcp_update.__doc__ or "",
        "inputSchema": _schema(
            {
                "name": {"type": "string"},
                "command": {"type": "string"},
                "args": {"type": "array", "items": {"type": "string"}},
                "env": {"type": "object", "additionalProperties": {"type": "string"}},
                "cwd": {"type": "string"},
                "strategy": {"type": "string", "enum": ["global", "per_project", "per_file"]},
                "required": {"type": "array", "items": {"type": "string"}},
                "db_file": {"type": "string"},
                "enabled": {"type": "boolean"},
                "description": {"type": "string"},
            },
            ["name"],
        ),
    },
    {"name": "mcp_remove", "description": mcp_remove.__doc__ or "", "inputSchema": _schema({"name": {"type": "string"}}, ["name"])},
    {"name": "mcp_restart", "description": mcp_restart.__doc__ or "", "inputSchema": _schema({"name": {"type": "string"}})},
    {"name": "mcp_set_context", "description": mcp_set_context.__doc__ or "", "inputSchema": _schema({"project": {"type": "string"}})},
    {
        "name": "mcp_tools",
        "description": mcp_tools.__doc__ or "",
        "inputSchema": _schema({"name": {"type": "string"}, "project": {"type": "string"}}, ["name"]),
    },
    {
        "name": "mcp_call",
        "description": mcp_call.__doc__ or "",
        "inputSchema": _schema(
            {
                "mcp": {"type": "string"},
                "tool": {"type": "string"},
                "args": {"type": "object"},
                "project": {"type": "string"},
            },
            ["mcp", "tool"],
        ),
    },
]

_HANDLERS = {
    "mcp_list": mcp_list,
    "mcp_get": mcp_get,
    "mcp_install": mcp_install,
    "mcp_update": mcp_update,
    "mcp_remove": mcp_remove,
    "mcp_restart": mcp_restart,
    "mcp_set_context": mcp_set_context,
    "mcp_tools": mcp_tools,
    "mcp_call": mcp_call,
}


def _write(payload: dict[str, Any]) -> None:
    line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    log.debug("SEND %s", line.strip()[:400])
    sys.stdout.write(line)
    sys.stdout.flush()


def _result(req_id: Any, result: dict[str, Any]) -> None:
    _write({"jsonrpc": "2.0", "id": req_id, "result": result})


def _error(req_id: Any, code: int, message: str) -> None:
    _write({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})


async def _dispatch_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    handler = _HANDLERS.get(name)
    if handler is None:
        raise KeyError(name)
    return await handler(**arguments)


async def _handle_method(method: str, params: dict[str, Any]) -> dict[str, Any]:
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments") or {}
        if tool_name not in _HANDLERS:
            raise LookupError(f"unknown tool: {tool_name}")
        result = await _dispatch_tool(tool_name, arguments)
        return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}
    if method == "ping":
        return {}
    raise LookupError(method)


class _Dispatcher:
    """单事件循环跑在后台线程，主线程阻塞读 stdin 后投递，保证实例的 loop 一致性。"""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _shutdown(self) -> None:
        if self._loop is not None and self._loop.is_running():
            try:
                fut = asyncio.run_coroutine_threadsafe(get_hub().close(), self._loop)
                fut.result(timeout=30)
            except Exception as exc:  # 关停失败不影响退出
                log.error("关停失败：%s", exc)
            else:
                log.info("McpHub 优雅关停完成")
            finally:
                self._loop.call_soon_threadsafe(self._loop.stop)

    def _handle_message(self, msg: Any) -> None:
        if not isinstance(msg, dict):
            log.debug("跳过非字典消息：%s", type(msg).__name__)
            return
        method = msg.get("method")
        req_id = msg.get("id")
        if method is None:
            return  # response/notification 无需处理
        if method == "initialize":
            requested = (msg.get("params") or {}).get("protocolVersion", PROTOCOL_VERSION)
            log.info("客户端握手：协议版本=%s", requested)
            _result(
                req_id,
                {
                    "protocolVersion": requested,
                    "capabilities": {"tools": {}},
                    "serverInfo": SERVER_INFO,
                },
            )
            return
        if method.startswith("notifications/"):
            log.debug("通知：%s", method)
            return

        params = msg.get("params") or {}
        try:
            fut = asyncio.run_coroutine_threadsafe(_handle_method(method, params), self._loop)
            result = fut.result(timeout=600)
            _result(req_id, result)
        except LookupError as exc:
            log.warning("未知方法/工具：%s", exc)
            _error(req_id, -32601, str(exc))
        except TimeoutError:
            log.error("请求超时：%s", method)
            _error(req_id, -32000, "request timeout")
        except Exception as exc:
            log.exception("处理请求异常：%s", method)
            _error(req_id, -32603, f"internal error: {exc}")

    def serve(self) -> None:
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
        log.info("McpHub 启动：pid=%d 日志文件=%s", os.getpid(), logger.log_file())
        log.debug("启动环境：argv=%s cwd=%s PYTHONHOME=%s PYTHONPATH=%s",
                  sys.argv, os.getcwd(), os.environ.get("PYTHONHOME"), os.environ.get("PYTHONPATH"))

        self._loop = asyncio.new_event_loop()
        worker = threading.Thread(target=self._run_loop, daemon=True)
        worker.start()

        try:
            while True:
                raw = sys.stdin.buffer.readline()
                if not raw:
                    log.info("stdin 关闭，开始关停")
                    break
                if raw.strip():
                    log.debug("RECV %s", raw[:400])
                text = raw.decode("utf-8", "replace").strip()
                if not text:
                    continue
                try:
                    msg = json.loads(text)
                except json.JSONDecodeError as exc:
                    log.warning("JSON 解析失败：%s raw=%s", exc, text[:200])
                    _error(None, -32700, "parse error")
                    continue
                msgs = msg if isinstance(msg, list) else [msg]
                for m in msgs:
                    try:
                        self._handle_message(m)
                    except Exception:
                        log.error("消息处理崩溃：%s", traceback.format_exc())
        finally:
            self._shutdown()


def main() -> None:
    _Dispatcher().serve()
