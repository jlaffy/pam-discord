from __future__ import annotations

import asyncio
import socket
from pathlib import Path

import aiohttp

from pam_discord.app_server import (
    CodexAppServer,
    load_shared_sessions,
    save_shared_sessions,
)


def test_shared_session_registry_round_trip(tmp_path: Path) -> None:
    save_shared_sessions(tmp_path, {"codex-1": 123})

    assert load_shared_sessions(tmp_path) == {"codex-1": 123}


def test_real_codex_app_server_handshake_and_broadcast(tmp_path: Path) -> None:
    async def exercise() -> None:
        with socket.socket() as candidate:
            candidate.bind(("127.0.0.1", 0))
            port = candidate.getsockname()[1]
        events: list[dict[str, object]] = []

        async def handle(event: dict[str, object]) -> None:
            events.append(event)

        server = CodexAppServer(f"ws://127.0.0.1:{port}", handle)
        await server.start("codex")
        try:
            result = await server.request("thread/list", {"limit": 1})
            assert isinstance(result, dict)
            assert isinstance(result.get("data"), list)
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(server.url) as client:
                    await client.send_json(
                        {
                            "method": "initialize",
                            "id": 0,
                            "params": {
                                "clientInfo": {"name": "test", "title": "Test", "version": "1"}
                            },
                        }
                    )
                    while (await client.receive_json()).get("id") != 0:
                        pass
                    await client.send_json({"method": "initialized", "params": {}})
                    await client.send_json(
                        {"method": "thread/start", "id": 1, "params": {"cwd": str(tmp_path)}}
                    )
                    while (await client.receive_json()).get("id") != 1:
                        pass
                    for _ in range(50):
                        if any(event.get("method") == "thread/started" for event in events):
                            break
                        await asyncio.sleep(0.02)
                    assert any(event.get("method") == "thread/started" for event in events)
        finally:
            await server.close()

    asyncio.run(exercise())
