from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from .bindings import resolve
from .config import (
    Bindings,
    HubConfig,
    McpDefinition,
    default_config_path,
    load_config,
    load_project_overlay,
    merge_overlay,
    save_config,
)
from . import logger
from .errors import HubError
from .manager import InstanceManager


class Hub:
    def __init__(self, config_path: str | Path | None = None) -> None:
        self.config_path = Path(config_path).expanduser() if config_path else default_config_path()
        self.config = load_config(self.config_path)
        log_cfg = self.config.defaults.logging
        logger.configure(
            log_dir=log_cfg.dir,
            level=log_cfg.level,
            stderr=log_cfg.stderr,
            backup_days=log_cfg.backup_days,
        )
        self.log = logger.get_logger()
        self.log.info("Hub 初始化：配置=%s 子MCP数=%d 日志文件=%s", self.config_path, len(self.config.mcps), logger.log_file())
        self.active_project: str | None = self.config.defaults.default_project
        self.manager = InstanceManager(
            call_timeout=float(self.config.defaults.call_timeout_seconds),
            start_timeout=float(self.config.defaults.start_timeout_seconds),
            idle_timeout=float(self.config.defaults.idle_timeout_minutes) * 60,
            max_instances=int(self.config.defaults.max_instances),
        )
        self._reaper: asyncio.Task[None] | None = None

    def _ensure_reaper(self) -> None:
        if self._reaper is None or self._reaper.done():
            self._reaper = asyncio.create_task(self._reap_loop(), name="mcp-hub:reaper")

    async def _reap_loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            try:
                reaped = await self.manager.reap_idle()
                if reaped:
                    self.log.info("空闲回收：关闭 %d 个实例", reaped)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.log.exception("空闲回收任务异常")

    def _project(self, project: str | None) -> str | None:
        return project or self.active_project

    def _effective_config(self, project: str | None) -> HubConfig:
        if not project:
            return self.config
        return merge_overlay(self.config, load_project_overlay(project))

    def _definition(self, name: str, project: str | None) -> McpDefinition:
        definition = self._effective_config(project).mcps.get(name)
        if definition is None:
            raise HubError("NOT_FOUND", f"mcp '{name}' is not installed", mcp=name)
        if not definition.enabled:
            raise HubError("DISABLED", f"mcp '{name}' is disabled", mcp=name)
        return definition

    def list(self) -> dict[str, Any]:
        counts = self.manager.stats()
        keys = self.manager.keys()
        items = []
        for name, definition in sorted(self.config.mcps.items()):
            items.append(
                {
                    "name": name,
                    "enabled": definition.enabled,
                    "strategy": definition.bindings.strategy,
                    "command": definition.command,
                    "args": definition.args,
                    "description": definition.description,
                    "instances": counts.get(name, 0),
                    "instance_keys": [key for key in keys if key.split(":", 1)[0] == name],
                }
            )
        return {
            "ok": True,
            "active_project": self.active_project,
            "config_path": str(self.config_path),
            "mcps": items,
        }

    def get(self, name: str) -> dict[str, Any]:
        definition = self.config.mcps.get(name)
        if definition is None:
            raise HubError("NOT_FOUND", f"mcp '{name}' is not installed", mcp=name)
        keys = [key for key in self.manager.keys() if key.split(":", 1)[0] == name]
        return {
            "ok": True,
            "mcp": definition.model_dump(mode="json"),
            "instance_keys": keys,
        }

    def install(
        self,
        *,
        name: str,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        strategy: str = "per_project",
        required: list[str] | None = None,
        db_file: str | None = None,
        enabled: bool = True,
        description: str | None = None,
    ) -> dict[str, Any]:
        if not name or any(char in name for char in ":/\\"):
            raise HubError("INVALID_NAME", "name must be non-empty and contain no ':' '/' '\\'", mcp=name)
        if name in self.config.mcps:
            raise HubError("ALREADY_EXISTS", f"mcp '{name}' already exists", mcp=name)
        definition = McpDefinition(
            name=name,
            command=command,
            args=args or [],
            env=env or {},
            cwd=cwd,
            enabled=enabled,
            description=description,
            bindings=Bindings(strategy=strategy, required=required or [], db_file=db_file),
        )
        self.config.mcps[name] = definition
        save_config(self.config, self.config_path)
        self.log.info("安装子 MCP：%s command=%s strategy=%s", name, command, strategy)
        return {
            "ok": True,
            "mcp": definition.model_dump(mode="json"),
            "config_path": str(self.config_path),
        }

    async def update(self, name: str, changes: dict[str, Any]) -> dict[str, Any]:
        definition = self.config.mcps.get(name)
        if definition is None:
            raise HubError("NOT_FOUND", f"mcp '{name}' is not installed", mcp=name)

        payload = definition.model_dump(mode="json")
        bindings_patch = changes.pop("bindings", None)
        for key in ("command", "args", "env", "cwd", "enabled", "description"):
            if changes.get(key) is not None:
                payload[key] = changes[key]
        for key in ("strategy", "required", "db_file"):
            if changes.get(key) is not None:
                payload["bindings"][key] = changes[key]
        if isinstance(bindings_patch, dict):
            payload["bindings"].update({key: value for key, value in bindings_patch.items() if value is not None})
        payload["name"] = name

        self.config.mcps[name] = McpDefinition.model_validate(payload)
        save_config(self.config, self.config_path)
        stopped = await self.manager.restart(name)
        self.log.info("更新子 MCP：%s 变更字段=%s 关闭实例=%d", name, list(changes.keys()), stopped)
        return {
            "ok": True,
            "mcp": self.config.mcps[name].model_dump(mode="json"),
            "restarted_instances": stopped,
        }

    async def remove(self, name: str) -> dict[str, Any]:
        if name not in self.config.mcps:
            raise HubError("NOT_FOUND", f"mcp '{name}' is not installed", mcp=name)
        del self.config.mcps[name]
        save_config(self.config, self.config_path)
        stopped = await self.manager.restart(name)
        self.log.info("卸载子 MCP：%s 关闭实例=%d", name, stopped)
        return {"ok": True, "removed": name, "stopped_instances": stopped}

    async def restart(self, name: str | None = None) -> dict[str, Any]:
        if name:
            if name not in self.config.mcps:
                raise HubError("NOT_FOUND", f"mcp '{name}' is not installed", mcp=name)
            stopped = await self.manager.restart(name)
            self.log.info("重启子 MCP：%s 关闭实例=%d", name, stopped)
            return {"ok": True, "restarted": name, "stopped_instances": stopped}
        await self.manager.close_all()
        self.log.info("重启全部子 MCP：已关闭所有实例")
        return {"ok": True, "restarted": "all"}

    def set_context(self, project: str | None = None) -> dict[str, Any]:
        if project:
            root = Path(project).expanduser().resolve()
            if not root.is_dir():
                raise HubError("INVALID_PROJECT", f"project directory does not exist: {root}")
            self.active_project = str(root)
        else:
            self.active_project = None
        self.config.defaults.default_project = self.active_project
        save_config(self.config, self.config_path)
        self.log.info("设置默认项目：%s", self.active_project)
        return {"ok": True, "active_project": self.active_project}

    async def tools(self, name: str, project: str | None = None) -> dict[str, Any]:
        self._ensure_reaper()
        project = self._project(project)
        definition = self._definition(name, project)
        resolved = resolve(definition, project)
        instance = await self.manager.acquire(resolved)
        return {
            "ok": True,
            "mcp": name,
            "instance": resolved.instance_key,
            "tools": [_tool_to_dict(tool) for tool in instance.tools],
        }

    async def call(
        self,
        name: str,
        tool: str,
        arguments: dict[str, Any] | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        self._ensure_reaper()
        project = self._project(project)
        definition = self._definition(name, project)
        resolved = resolve(definition, project)
        instance = await self.manager.acquire(resolved)
        self.log.info("调用工具：%s.%s project=%s instance=%s", name, tool, project, resolved.instance_key)
        try:
            result = await instance.call(tool, arguments)
        except Exception as exc:
            self.log.warning("工具调用失败：%s.%s error=%s", name, tool, exc)
            raise
        finally:
            self.manager.touch(resolved.instance_key)
        return _result_to_dict(result, resolved)

    async def close(self) -> None:
        if self._reaper is not None:
            self._reaper.cancel()
        await self.manager.close_all()
        self.log.info("Hub 关闭：所有实例已清理")


def _pick(obj: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if hasattr(obj, name):
            return getattr(obj, name)
    return default


def _tool_to_dict(tool: Any) -> dict[str, Any]:
    return {
        "name": _pick(tool, "name"),
        "description": _pick(tool, "description"),
        "input_schema": _pick(tool, "input_schema", "inputSchema"),
    }


def _result_to_dict(result: Any, resolved: Any) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    for block in _pick(result, "content", default=[]) or []:
        block_type = _pick(block, "type")
        if block_type == "text":
            content.append({"type": "text", "text": _pick(block, "text", default="")})
        else:
            content.append({"type": block_type or type(block).__name__})
    payload: dict[str, Any] = {
        "ok": True,
        "mcp": resolved.name,
        "instance": resolved.instance_key,
        "is_error": bool(_pick(result, "isError", "is_error", default=False)),
        "content": content,
    }
    structured = _pick(result, "structuredContent", "structured_content")
    if structured is not None:
        payload["structured_content"] = structured
    return payload
