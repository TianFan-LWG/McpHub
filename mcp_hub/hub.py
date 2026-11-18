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
from .errors import HubError
from .manager import InstanceManager


class Hub:
    def __init__(self, config_path: str | Path | None = None) -> None:
        self.config_path = Path(config_path).expanduser() if config_path else default_config_path()
        self.config = load_config(self.config_path)
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
                await self.manager.reap_idle()
            except Exception:
                pass

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
        return {"ok": True, "removed": name, "stopped_instances": stopped}

    async def restart(self, name: str | None = None) -> dict[str, Any]:
        if name:
            if name not in self.config.mcps:
                raise HubError("NOT_FOUND", f"mcp '{name}' is not installed", mcp=name)
            stopped = await self.manager.restart(name)
            return {"ok": True, "restarted": name, "stopped_instances": stopped}
        await self.manager.close_all()
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
        try:
            result = await instance.call(tool, arguments)
        finally:
            self.manager.touch(resolved.instance_key)
        return _result_to_dict(result, resolved)

    async def close(self) -> None:
        if self._reaper is not None:
            self._reaper.cancel()
        await self.manager.close_all()


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
