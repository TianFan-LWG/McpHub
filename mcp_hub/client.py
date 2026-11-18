from __future__ import annotations

import asyncio
import os
import shutil
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .errors import HubError


def resolve_command(command: str) -> str:
    if os.name != "nt":
        return command
    return shutil.which(command) or command


def _format_error(exc: BaseException) -> str:
    group_type = getattr(__builtins__, "BaseExceptionGroup", None) or BaseExceptionGroup
    if isinstance(exc, group_type):
        parts = [_format_error(item) for item in exc.exceptions]
        return "; ".join(part for part in parts if part) or exc.__class__.__name__
    text = str(exc).strip()
    return text or exc.__class__.__name__


class StdioSubMcp:
    """One child MCP process. All session I/O happens inside the worker task,
    which is required because anyio streams are bound to the task that opened them."""

    def __init__(
        self,
        *,
        name: str,
        command: str,
        args: list[str],
        env: dict[str, str],
        cwd: str | None,
        call_timeout: float = 60.0,
        start_timeout: float = 90.0,
    ) -> None:
        self.name = name
        self.command = command
        self.args = args
        self.env = env
        self.cwd = cwd
        self.call_timeout = call_timeout
        self.start_timeout = start_timeout
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._ready = asyncio.Event()
        self._start_error: BaseException | None = None
        self._closed = False
        self._worker: asyncio.Task[None] | None = None
        self._tools: list[Any] = []

    @property
    def tools(self) -> list[Any]:
        return list(self._tools)

    @property
    def closed(self) -> bool:
        return self._closed

    async def start(self) -> None:
        self._worker = asyncio.create_task(self._run(), name=f"mcp-hub:{self.name}")
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=self.start_timeout)
        except asyncio.TimeoutError as exc:
            await self.stop()
            raise HubError(
                "START_TIMEOUT",
                f"mcp '{self.name}' did not become ready in {self.start_timeout:.0f}s",
                mcp=self.name,
            ) from exc
        if self._start_error is not None:
            message = _format_error(self._start_error)
            await self.stop()
            raise HubError(
                "START_FAILED",
                f"mcp '{self.name}' failed to start: {message}",
                mcp=self.name,
            )

    async def call(self, tool: str, arguments: dict[str, Any] | None = None) -> Any:
        if self._closed:
            raise HubError("INSTANCE_CLOSED", f"mcp '{self.name}' instance is closed", mcp=self.name)
        if self._start_error is not None:
            raise HubError(
                "START_FAILED",
                f"mcp '{self.name}' is not available: {_format_error(self._start_error)}",
                mcp=self.name,
            )
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        await self._queue.put((future, tool, arguments or {}))
        try:
            return await asyncio.wait_for(future, timeout=self.call_timeout)
        except asyncio.TimeoutError as exc:
            future.cancel()
            raise HubError(
                "TIMEOUT",
                f"tool '{tool}' on '{self.name}' exceeded {self.call_timeout:.0f}s",
                mcp=self.name,
                tool=tool,
            ) from exc

    async def stop(self) -> None:
        already_closed = self._closed
        self._closed = True
        if self._worker is None:
            return
        if not self._worker.done() and not already_closed:
            try:
                await self._queue.put(None)
            except Exception:
                pass
        try:
            await asyncio.wait_for(asyncio.shield(self._worker), timeout=10)
        except asyncio.TimeoutError:
            self._worker.cancel()
            try:
                await self._worker
            except BaseException:
                pass
        except BaseException:
            pass

    async def _run(self) -> None:
        try:
            params = StdioServerParameters(
                command=resolve_command(self.command),
                args=list(self.args),
                env={**os.environ, **self.env},
                cwd=self.cwd,
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    self._tools = list(listed.tools)
                    self._ready.set()
                    while True:
                        item = await self._queue.get()
                        if item is None:
                            break
                        future, tool, arguments = item
                        if future.cancelled():
                            continue
                        try:
                            result = await session.call_tool(tool, arguments)
                        except Exception as exc:
                            if not future.done():
                                future.set_exception(exc)
                        else:
                            if not future.done():
                                future.set_result(result)
        except Exception as exc:
            self._start_error = exc
            self._ready.set()
            self._fail_pending(exc)
        finally:
            self._closed = True
            self._ready.set()
            self._fail_pending(
                HubError("INSTANCE_CLOSED", f"mcp '{self.name}' instance is closed", mcp=self.name)
            )

    def _fail_pending(self, exc: BaseException) -> None:
        while True:
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if item is None:
                continue
            future = item[0]
            if not future.done():
                future.set_exception(exc)
