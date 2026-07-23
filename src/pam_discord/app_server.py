from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path

import aiohttp

NotificationHandler = Callable[[dict[str, object]], Awaitable[None]]


class CodexAppServer:
    def __init__(self, url: str, handler: NotificationHandler) -> None:
        self.url = url
        self.handler = handler
        self._process: asyncio.subprocess.Process | None = None
        self._session: aiohttp.ClientSession | None = None
        self._socket: aiohttp.ClientWebSocketResponse | None = None
        self._reader: asyncio.Task[None] | None = None
        self._next_id = 1
        self._pending: dict[int, asyncio.Future[object]] = {}

    async def start(self, codex_binary: str) -> None:
        binary = shutil.which(codex_binary)
        if binary is None:
            raise RuntimeError(f"Codex executable not found: {codex_binary}")
        self._session = aiohttp.ClientSession()
        try:
            self._socket = await self._session.ws_connect(self.url)
        except aiohttp.ClientError:
            self._socket = None
        if self._socket is None:
            await self._start_process(binary)
        self._reader = asyncio.create_task(self._read_loop())
        await self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "pam_discord",
                    "title": "pam",
                    "version": "0.2.0",
                }
            },
        )
        await self.notify("initialized", {})

    async def _start_process(self, binary: str) -> None:
        self._process = await asyncio.create_subprocess_exec(
            binary,
            "app-server",
            "--listen",
            self.url,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        assert self._session is not None
        for _ in range(50):
            try:
                self._socket = await self._session.ws_connect(self.url)
                break
            except aiohttp.ClientError:
                if self._process.returncode is not None:
                    detail = await self._process.stderr.read() if self._process.stderr else b""
                    raise RuntimeError(detail.decode().strip() or "Codex app-server stopped")
                await asyncio.sleep(0.1)
        if self._socket is None:
            raise RuntimeError("Timed out connecting to Codex app-server")

    async def close(self) -> None:
        if self._socket is not None:
            await self._socket.close()
        if self._reader is not None:
            self._reader.cancel()
        if self._session is not None:
            await self._session.close()
        if self._process is not None and self._process.returncode is None:
            self._process.terminate()
            await self._process.wait()

    async def request(self, method: str, params: dict[str, object]) -> object:
        if self._socket is None:
            raise RuntimeError("Codex app-server is not connected")
        request_id = self._next_id
        self._next_id += 1
        future: asyncio.Future[object] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        await self._socket.send_json({"method": method, "id": request_id, "params": params})
        try:
            return await asyncio.wait_for(future, timeout=30)
        finally:
            self._pending.pop(request_id, None)

    async def notify(self, method: str, params: dict[str, object]) -> None:
        if self._socket is None:
            raise RuntimeError("Codex app-server is not connected")
        await self._socket.send_json({"method": method, "params": params})

    async def respond(self, request_id: int, result: dict[str, object]) -> None:
        """Answer a request initiated by the app server."""
        if self._socket is None:
            raise RuntimeError("Codex app-server is not connected")
        await self._socket.send_json({"id": request_id, "result": result})

    async def _read_loop(self) -> None:
        assert self._socket is not None
        async for message in self._socket:
            if message.type != aiohttp.WSMsgType.TEXT:
                continue
            value = json.loads(message.data)
            request_id = value.get("id")
            if isinstance(request_id, int) and request_id in self._pending:
                future = self._pending.pop(request_id)
                if "error" in value:
                    future.set_exception(RuntimeError(str(value["error"])))
                else:
                    future.set_result(value.get("result"))
            elif isinstance(value.get("method"), str):
                asyncio.create_task(self.handler(value))


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
