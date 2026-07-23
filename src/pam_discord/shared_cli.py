from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import uuid
from pathlib import Path

import aiohttp

from .config import load_config
from .setup import DEFAULT_STATE_DIR, project_add


def _config(state_dir: Path):
    path = state_dir.expanduser().resolve() / "config.toml"
    if not path.exists():
        raise SystemExit("pam is not configured. Run `./pam setup` first.")
    return load_config(path)


def _project_for_path(config, path: Path) -> Path:
    workspaces = {
        item.workspace
        for item in config.guilds.values()
        if path == item.workspace or path.is_relative_to(item.workspace)
    }
    if not workspaces:
        raise SystemExit(
            f"This directory is not connected to a pam project: {path}\n"
            "Run `./pam project add /path/to/project`."
        )
    return max(workspaces, key=lambda item: len(item.parts))


async def _wait_for_app_server(url: str) -> None:
    async with aiohttp.ClientSession() as session:
        for _ in range(100):
            try:
                socket = await session.ws_connect(url)
                await socket.close()
                return
            except aiohttp.ClientError:
                await asyncio.sleep(0.1)
    raise SystemExit("pam did not start its Codex app-server. Check `pam service logs`.")


def codex(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--pam-state-dir", type=Path, default=DEFAULT_STATE_DIR)
    known, codex_args = parser.parse_known_args(argv)
    state_dir = known.pam_state_dir.expanduser().resolve()
    cwd = Path.cwd().resolve()
    try:
        config = _config(state_dir)
        workspace = _project_for_path(config, cwd)
    except SystemExit:
        if not (state_dir / "identity.json").exists():
            raise
        print(f"This directory is not connected to pam yet: {cwd}")
        project_add([str(cwd), "--state-dir", str(state_dir)])
        config = _config(state_dir)
        workspace = _project_for_path(config, cwd)
    binary = shutil.which(config.codex_binary)
    if binary is None:
        raise SystemExit(f"Codex executable not found: {config.codex_binary}")
    asyncio.run(_wait_for_app_server(config.codex_app_server_url))
    command = [
        binary,
        "--remote",
        config.codex_app_server_url,
        "-C",
        str(cwd),
        *codex_args,
    ]
    result = subprocess.run(command, check=False)
    raise SystemExit(result.returncode)


def resume(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--pam-state-dir", type=Path, default=DEFAULT_STATE_DIR)
    known, codex_args = parser.parse_known_args(argv)
    config = _config(known.pam_state_dir)
    workspace = _project_for_path(config, Path.cwd().resolve())
    binary = shutil.which(config.codex_binary)
    if binary is None:
        raise SystemExit(f"Codex executable not found: {config.codex_binary}")
    try:
        conversations = asyncio.run(
            _project_conversations(config.codex_app_server_url, workspace)
        )
    except (aiohttp.ClientError, OSError) as exc:
        raise SystemExit("pam is not running. Start it with `pam service start`.") from exc
    if not conversations:
        raise SystemExit(f"No active Codex conversations found in {workspace}")
    use_last = "--last" in codex_args
    codex_args = [value for value in codex_args if value != "--last"]
    if use_last:
        selected = conversations[0]
    else:
        print(f"Active conversations in {workspace}:\n")
        for index, conversation in enumerate(conversations, start=1):
            cwd = Path(str(conversation["cwd"])).resolve()
            relative = "." if cwd == workspace else str(cwd.relative_to(workspace))
            title = _conversation_title(conversation)
            print(f"  {index}. {title}  [{relative}]")
        choice = input("\nConversation number: ").strip()
        try:
            selected = conversations[int(choice) - 1]
        except (ValueError, IndexError) as exc:
            raise SystemExit("Invalid conversation number") from exc
    command = [
        binary,
        "-C",
        str(Path(str(selected["cwd"])).resolve()),
        "resume",
        "--include-non-interactive",
        str(selected["id"]),
        *codex_args,
    ]
    result = subprocess.run(command, check=False)
    raise SystemExit(result.returncode)


async def _request(url: str, method: str, params: dict[str, object]) -> object:
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(url) as socket:
            await socket.send_json(
                {
                    "method": "initialize",
                    "id": 0,
                    "params": {
                        "clientInfo": {
                            "name": "pam_link",
                            "title": "pam Link",
                            "version": "0.2.0",
                        }
                    },
                }
            )
            while True:
                value = json.loads((await socket.receive()).data)
                if value.get("id") == 0:
                    break
            await socket.send_json({"method": "initialized", "params": {}})
            await socket.send_json({"method": method, "id": 1, "params": params})
            while True:
                value = json.loads((await socket.receive()).data)
                if value.get("id") == 1:
                    if "error" in value:
                        raise RuntimeError(str(value["error"]))
                    return value.get("result")


def _conversation_title(value: dict[str, object]) -> str:
    name = str(value.get("name") or "").strip()
    if name:
        return " ".join(name.split())[:80]
    lines = [
        line.strip()
        for line in str(value.get("preview") or "").splitlines()
        if line.strip() and line.strip() != "Follow this project's instructions."
    ]
    return " ".join((lines[0] if lines else "Untitled conversation").split())[:80]


async def _project_conversations(url: str, workspace: Path) -> list[dict[str, object]]:
    conversations: list[dict[str, object]] = []
    cursor: str | None = None
    while True:
        params: dict[str, object] = {
            "sourceKinds": ["cli", "exec", "appServer"],
            "archived": False,
            "limit": 100,
            "sortKey": "recency_at",
            "sortDirection": "desc",
        }
        if cursor is not None:
            params["cursor"] = cursor
        result = await _request(url, "thread/list", params)
        if not isinstance(result, dict):
            break
        for value in result.get("data", []):
            if not isinstance(value, dict) or not isinstance(value.get("cwd"), str):
                continue
            cwd = Path(str(value["cwd"])).resolve()
            if cwd == workspace or cwd.is_relative_to(workspace):
                conversations.append(value)
        next_cursor = result.get("nextCursor")
        if not isinstance(next_cursor, str) or not next_cursor:
            break
        cursor = next_cursor
    return conversations


async def _link_latest(url: str, cwd: Path) -> str:
    result = await _request(
        url,
        "thread/list",
        {"cwd": str(cwd), "limit": 1, "sortKey": "recency_at", "sortDirection": "desc"},
    )
    threads = result.get("data", []) if isinstance(result, dict) else []
    if not threads:
        raise SystemExit(f"No existing Codex conversation found in {cwd}")
    thread_id = str(threads[0]["id"])
    return thread_id


def link(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description="Link the latest Codex conversation to pam")
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    parser.add_argument("--cwd", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    config = _config(args.state_dir)
    cwd = args.cwd.expanduser().resolve()
    _project_for_path(config, cwd)
    try:
        thread_id = asyncio.run(_link_latest(config.codex_app_server_url, cwd))
    except (aiohttp.ClientError, OSError) as exc:
        raise SystemExit("pam is not running. Start it with `./pam service start`.") from exc
    request_dir = args.state_dir.expanduser().resolve() / "link-requests"
    request_dir.mkdir(parents=True, exist_ok=True)
    request_path = request_dir / f"{uuid.uuid4()}.json"
    request_path.write_text(
        json.dumps({"thread_id": thread_id, "cwd": str(cwd)}, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Linked Codex conversation {thread_id} to pam.")
