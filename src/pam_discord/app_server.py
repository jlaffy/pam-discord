from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path

NotificationHandler = Callable[[dict[str, object]], Awaitable[None]]


class CodexAppServer:
    """An owned Codex app-server using its supported JSONL stdio transport."""

    def __init__(self, url: str, handler: NotificationHandler) -> None:
        # Keep url in the API/config for compatibility with existing installations. Pam
        # deliberately owns a stdio server now; websocket listening is experimental and
        # allowed stale, independently-owned processes to outlive Pam.
        self.url = url
        self.handler = handler
        self._process: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task[None] | None = None
        self._stderr_reader: asyncio.Task[None] | None = None
        self._handler_tasks: set[asyncio.Task[None]] = set()
        self._next_id = 1
        self._pending: dict[int, asyncio.Future[object]] = {}
        self._binary: str | None = None
        self._connect_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._closing = False

    async def start(self, codex_binary: str) -> None:
        binary = shutil.which(codex_binary)
        if binary is None:
            raise RuntimeError(f"Codex executable not found: {codex_binary}")
        self._binary = binary
        await self._connect()

    async def _connect(self) -> None:
        if self._binary is None:
            raise RuntimeError("Codex app-server has not been started")
        await self._discard_process()
        self._process = await asyncio.create_subprocess_exec(
            self._binary,
            "app-server",
            "--stdio",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # `thread/read(includeTurns=true)` returns one JSONL record containing the
            # complete conversation. Real histories routinely exceed asyncio's 64 KiB
            # default StreamReader limit.
            limit=64 * 1024 * 1024,
        )
        self._reader = asyncio.create_task(self._read_loop(self._process))
        self._stderr_reader = asyncio.create_task(self._drain_stderr(self._process))
        await self._request_connected(
            "initialize",
            {
                "clientInfo": {
                    "name": "pam_discord",
                    "title": "pam",
                    "version": "0.2.0",
                }
            },
        )
        await self._send({"method": "initialized", "params": {}})

    def _connected(self) -> bool:
        return (
            self._process is not None
            and self._process.returncode is None
            and self._process.stdin is not None
            and not self._process.stdin.is_closing()
            and self._reader is not None
            and not self._reader.done()
        )

    async def _ensure_connected(self) -> None:
        if self._connected():
            return
        async with self._connect_lock:
            if not self._connected():
                await self._connect()

    async def close(self) -> None:
        self._closing = True
        self._fail_pending(ConnectionError("Codex app-server closed"))
        await self._discard_process()
        for task in tuple(self._handler_tasks):
            task.cancel()
        if self._handler_tasks:
            await asyncio.gather(*self._handler_tasks, return_exceptions=True)

    async def _discard_process(self) -> None:
        process, reader, stderr_reader = self._process, self._reader, self._stderr_reader
        self._process = None
        self._reader = None
        self._stderr_reader = None
        current = asyncio.current_task()
        for task in (reader, stderr_reader):
            if task is not None and task is not current:
                task.cancel()
        if process is not None and process.returncode is None:
            process.terminate()
            with contextlib.suppress(ProcessLookupError, asyncio.TimeoutError):
                await asyncio.wait_for(process.wait(), timeout=5)
            if process.returncode is None:
                process.kill()
                await process.wait()
        for task in (reader, stderr_reader):
            if task is not None and task is not current:
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    async def request(self, method: str, params: dict[str, object]) -> object:
        await self._ensure_connected()
        return await self._request_connected(method, params)

    async def _request_connected(self, method: str, params: dict[str, object]) -> object:
        request_id = self._next_id
        self._next_id += 1
        future: asyncio.Future[object] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._send({"method": method, "id": request_id, "params": params})
            return await asyncio.wait_for(future, timeout=30)
        finally:
            self._pending.pop(request_id, None)

    async def notify(self, method: str, params: dict[str, object]) -> None:
        await self._ensure_connected()
        await self._send({"method": method, "params": params})

    async def respond(self, request_id: int, result: dict[str, object]) -> None:
        await self._ensure_connected()
        await self._send({"id": request_id, "result": result})

    async def _send(self, value: dict[str, object]) -> None:
        process = self._process
        if process is None or process.stdin is None or process.returncode is not None:
            raise ConnectionError("Codex app-server is not connected")
        data = (json.dumps(value, separators=(",", ":")) + "\n").encode()
        async with self._write_lock:
            process.stdin.write(data)
            await process.stdin.drain()

    def _complete_pending(
        self, request_id: int, *, result: object = None, error: BaseException | None = None
    ) -> None:
        future = self._pending.pop(request_id, None)
        if future is None or future.done():
            return
        if error is not None:
            future.set_exception(error)
        else:
            future.set_result(result)

    def _fail_pending(self, error: BaseException) -> None:
        # Pop before completion so timeout, reconnect and reader shutdown cannot
        # complete the same Future twice (the old InvalidStateError).
        pending, self._pending = self._pending, {}
        for future in pending.values():
            if not future.done():
                future.set_exception(error)

    async def _read_loop(self, process: asyncio.subprocess.Process) -> None:
        assert process.stdout is not None
        try:
            while line := await process.stdout.readline():
                value = json.loads(line)
                request_id = value.get("id")
                if isinstance(request_id, int) and (
                    "result" in value or "error" in value
                ):
                    error = (
                        RuntimeError(str(value["error"])) if "error" in value else None
                    )
                    self._complete_pending(
                        request_id, result=value.get("result"), error=error
                    )
                elif isinstance(value.get("method"), str):
                    task = asyncio.create_task(self.handler(value))
                    self._handler_tasks.add(task)
                    task.add_done_callback(self._handler_tasks.discard)
        finally:
            if self._process is process:
                self._process = None
            if not self._closing:
                self._fail_pending(ConnectionError("Codex app-server connection closed"))

    @staticmethod
    async def _drain_stderr(process: asyncio.subprocess.Process) -> None:
        assert process.stderr is not None
        while await process.stderr.readline():
            pass


def shared_session_registry(workspace: Path) -> Path:
    return workspace / ".pam" / "shared-sessions.json"


def load_shared_sessions(workspace: Path) -> dict[str, int]:
    path = shared_session_registry(workspace)
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return {str(key): int(item) for key, item in value.items()}


def save_shared_sessions(workspace: Path, sessions: dict[str, int]) -> None:
    path = shared_session_registry(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sessions, indent=2, sort_keys=True) + "\n", encoding="utf-8")
