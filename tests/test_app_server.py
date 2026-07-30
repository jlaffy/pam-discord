from __future__ import annotations

import asyncio
from pathlib import Path

from pam_discord.app_server import (
    CodexAppServer,
    load_shared_sessions,
    save_shared_sessions,
)


def test_shared_session_registry_round_trip(tmp_path: Path) -> None:
    save_shared_sessions(tmp_path, {"codex-1": 123})

    assert load_shared_sessions(tmp_path) == {"codex-1": 123}


def test_pending_request_is_completed_exactly_once() -> None:
    async def exercise() -> None:
        async def handle(event: dict[str, object]) -> None:
            pass

        server = CodexAppServer("ws://127.0.0.1:1", handle)
        future: asyncio.Future[object] = asyncio.get_running_loop().create_future()
        server._pending[1] = future
        server._complete_pending(1, result={"ok": True})
        server._complete_pending(1, error=ConnectionError("late disconnect"))
        server._fail_pending(ConnectionError("reconnect"))
        assert await future == {"ok": True}
        assert server._pending == {}

    asyncio.run(exercise())


def test_real_codex_app_server_handshake_and_broadcast(tmp_path: Path) -> None:
    async def exercise() -> None:
        events: list[dict[str, object]] = []

        async def handle(event: dict[str, object]) -> None:
            events.append(event)

        server = CodexAppServer("stdio://", handle)
        await server.start("codex")
        try:
            result = await server.request("thread/list", {"limit": 1})
            assert isinstance(result, dict)
            assert isinstance(result.get("data"), list)
            await server.request("thread/start", {"cwd": str(tmp_path)})
            for _ in range(50):
                if any(event.get("method") == "thread/started" for event in events):
                    break
                await asyncio.sleep(0.02)
            assert any(event.get("method") == "thread/started" for event in events)
        finally:
            await server.close()

    asyncio.run(exercise())
