from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from . import logger
from .bindings import ResolvedBinding

if TYPE_CHECKING:
    from .client import StdioSubMcp


class InstanceManager:
    def __init__(
        self,
        *,
        call_timeout: float = 60.0,
        start_timeout: float = 90.0,
        idle_timeout: float = 1800.0,
        max_instances: int = 16,
    ) -> None:
        self.call_timeout = call_timeout
        self.start_timeout = start_timeout
        self.idle_timeout = idle_timeout
        self.max_instances = max_instances
        self._instances: dict[str, StdioSubMcp] = {}
        self._last_used: dict[str, float] = {}
        self._lock = asyncio.Lock()
        self.log = logger.get_logger()

    async def acquire(self, resolved: ResolvedBinding) -> StdioSubMcp:
        key = resolved.instance_key
        async with self._lock:
            instance = self._instances.get(key)
            if instance is not None and instance.closed:
                self.log.warning("实例已崩溃，移除后重建：%s", key)
                self._instances.pop(key, None)
                self._last_used.pop(key, None)
                instance = None
            if instance is None:
                await self._evict_if_needed()
                from .client import StdioSubMcp

                instance = StdioSubMcp(
                    name=resolved.name,
                    command=resolved.command,
                    args=resolved.args,
                    env=resolved.env,
                    cwd=resolved.cwd,
                    call_timeout=self.call_timeout,
                    start_timeout=self.start_timeout,
                )
                self.log.info("启动子 MCP 实例：%s command=%s args=%s", key, resolved.command, resolved.args)
                await instance.start()
                self._instances[key] = instance
                self.log.info("子 MCP 实例就绪：%s 工具数=%d", key, len(instance.tools))
            else:
                self.log.debug("复用实例：%s", key)
            self._last_used[key] = time.monotonic()
            return instance

    async def _evict_if_needed(self) -> None:
        while len(self._instances) >= self.max_instances and self._last_used:
            key = min(self._last_used, key=lambda item: self._last_used[item])
            instance = self._instances.pop(key, None)
            self._last_used.pop(key, None)
            if instance is not None:
                self.log.info("LRU 淘汰实例：%s（已达上限 %d）", key, self.max_instances)
                await instance.stop()

    def touch(self, key: str) -> None:
        if key in self._instances:
            self._last_used[key] = time.monotonic()

    async def restart(self, name: str) -> int:
        async with self._lock:
            keys = [key for key in self._instances if key.split(":", 1)[0] == name]
            instances: list[StdioSubMcp] = []
            for key in keys:
                instance = self._instances.pop(key, None)
                self._last_used.pop(key, None)
                if instance is not None:
                    instances.append(instance)
        if instances:
            self.log.info("重启子 MCP %s：关闭 %d 个实例", name, len(instances))
        for instance in instances:
            await instance.stop()
        return len(instances)

    async def close_all(self) -> None:
        async with self._lock:
            instances = list(self._instances.values())
            self._instances.clear()
            self._last_used.clear()
        if instances:
            self.log.info("close_all：关闭 %d 个实例", len(instances))
        for instance in instances:
            await instance.stop()

    async def reap_idle(self) -> int:
        now = time.monotonic()
        async with self._lock:
            stale = [key for key, used in self._last_used.items() if now - used > self.idle_timeout]
            instances: list[StdioSubMcp] = []
            for key in stale:
                instance = self._instances.pop(key, None)
                self._last_used.pop(key, None)
                if instance is not None:
                    instances.append(instance)
        if instances:
            self.log.info("空闲回收：关闭 %d 个超时实例（idle=%ds）", len(instances), int(self.idle_timeout))
        for instance in instances:
            await instance.stop()
        return len(instances)

    def stats(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for key in self._instances:
            name = key.split(":", 1)[0]
            counts[name] = counts.get(name, 0) + 1
        return counts

    def keys(self) -> list[str]:
        return list(self._instances)
