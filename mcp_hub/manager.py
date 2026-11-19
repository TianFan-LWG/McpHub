from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

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

    async def acquire(self, resolved: ResolvedBinding) -> StdioSubMcp:
        key = resolved.instance_key
        async with self._lock:
            instance = self._instances.get(key)
            if instance is not None and instance.closed:
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
                await instance.start()
                self._instances[key] = instance
            self._last_used[key] = time.monotonic()
            return instance

    async def _evict_if_needed(self) -> None:
        while len(self._instances) >= self.max_instances and self._last_used:
            key = min(self._last_used, key=lambda item: self._last_used[item])
            instance = self._instances.pop(key, None)
            self._last_used.pop(key, None)
            if instance is not None:
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
        for instance in instances:
            await instance.stop()
        return len(instances)

    async def close_all(self) -> None:
        async with self._lock:
            instances = list(self._instances.values())
            self._instances.clear()
            self._last_used.clear()
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
