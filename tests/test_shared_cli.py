from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from pam_discord.setup import _write_config
from pam_discord.shared_cli import codex, link, resume


def test_pam_codex_connects_real_cli_to_shared_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_dir = tmp_path / "state"
    workspace = tmp_path / "project"
    state_dir.mkdir()
    workspace.mkdir()
    _write_config(
        state_dir / "config.toml",
        state_dir=state_dir,
        user_id=1,
        channel_id=2,
        guild_id=3,
        workspace=workspace,
    )
    commands: list[list[str]] = []
    monkeypatch.chdir(workspace)
    monkeypatch.setattr("pam_discord.shared_cli.shutil.which", lambda _binary: "/bin/codex")

    async def ready(_url: str) -> None:
        return None

    monkeypatch.setattr("pam_discord.shared_cli._wait_for_app_server", ready)

    def run(command: list[str], **_kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("pam_discord.shared_cli.subprocess.run", run)

    with pytest.raises(SystemExit, match="0"):
        codex(["--pam-state-dir", str(state_dir), "--yolo"])

    assert commands == [
        [
            "/bin/codex",
            "--remote",
            "ws://127.0.0.1:45832",
            "-C",
            str(workspace),
            "--yolo",
        ]
    ]


def test_pam_codex_preserves_project_subdirectory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_dir = tmp_path / "state"
    workspace = tmp_path / "project"
    subdirectory = workspace / "packages" / "api"
    state_dir.mkdir()
    subdirectory.mkdir(parents=True)
    _write_config(
        state_dir / "config.toml",
        state_dir=state_dir,
        user_id=1,
        channel_id=2,
        guild_id=3,
        workspace=workspace,
    )
    commands: list[list[str]] = []
    monkeypatch.chdir(subdirectory)
    monkeypatch.setattr("pam_discord.shared_cli.shutil.which", lambda _binary: "/bin/codex")

    async def ready(_url: str) -> None:
        return None

    monkeypatch.setattr("pam_discord.shared_cli._wait_for_app_server", ready)
    monkeypatch.setattr(
        "pam_discord.shared_cli.subprocess.run",
        lambda command, **_kwargs: (
            commands.append(command) or subprocess.CompletedProcess(command, 0)
        ),
    )

    with pytest.raises(SystemExit, match="0"):
        codex(["--pam-state-dir", str(state_dir)])

    assert commands == [
        [
            "/bin/codex",
            "--remote",
            "ws://127.0.0.1:45832",
            "-C",
            str(subdirectory),
        ]
    ]


def test_link_queues_latest_conversation_for_running_pam(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_dir = tmp_path / "state"
    workspace = tmp_path / "project"
    state_dir.mkdir()
    workspace.mkdir()
    _write_config(
        state_dir / "config.toml",
        state_dir=state_dir,
        user_id=1,
        channel_id=2,
        guild_id=3,
        workspace=workspace,
    )

    async def latest(_url: str, _cwd: Path) -> str:
        return "codex-thread-1"

    monkeypatch.setattr("pam_discord.shared_cli._link_latest", latest)

    link(["--state-dir", str(state_dir), "--cwd", str(workspace)])

    requests = list((state_dir / "link-requests").glob("*.json"))
    assert len(requests) == 1
    assert '"thread_id": "codex-thread-1"' in requests[0].read_text()


def test_pam_codex_connects_current_directory_when_needed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_dir = tmp_path / "state"
    workspace = tmp_path / "new-project"
    state_dir.mkdir()
    workspace.mkdir()
    (state_dir / "identity.json").write_text(json.dumps({"discord_user_id": 1}))
    added: list[list[str]] = []

    def add_project(args: list[str]) -> None:
        added.append(args)
        _write_config(
            state_dir / "config.toml",
            state_dir=state_dir,
            user_id=1,
            channel_id=2,
            guild_id=3,
            workspace=workspace,
        )

    monkeypatch.chdir(workspace)
    monkeypatch.setattr("pam_discord.shared_cli.project_add", add_project)
    monkeypatch.setattr("pam_discord.shared_cli.shutil.which", lambda _binary: "/bin/codex")

    async def ready(_url: str) -> None:
        return None

    monkeypatch.setattr("pam_discord.shared_cli._wait_for_app_server", ready)
    monkeypatch.setattr(
        "pam_discord.shared_cli.subprocess.run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0),
    )

    with pytest.raises(SystemExit, match="0"):
        codex(["--pam-state-dir", str(state_dir)])

    assert added == [[str(workspace), "--state-dir", str(state_dir)]]


def test_pam_resume_includes_discord_started_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_dir = tmp_path / "state"
    workspace = tmp_path / "project"
    state_dir.mkdir()
    workspace.mkdir()
    _write_config(
        state_dir / "config.toml",
        state_dir=state_dir,
        user_id=1,
        channel_id=2,
        guild_id=3,
        workspace=workspace,
    )
    commands: list[list[str]] = []
    monkeypatch.chdir(workspace)
    monkeypatch.setattr("pam_discord.shared_cli.shutil.which", lambda _binary: "/bin/codex")
    monkeypatch.setattr(
        "pam_discord.shared_cli.subprocess.run",
        lambda command, **_kwargs: (
            commands.append(command) or subprocess.CompletedProcess(command, 0)
        ),
    )
    async def conversations(_url: str, _workspace: Path) -> list[dict[str, object]]:
        return [
            {
                "id": "discord-conversation",
                "cwd": str(workspace),
                "name": "Discord conversation",
            }
        ]

    monkeypatch.setattr("pam_discord.shared_cli._project_conversations", conversations)
    monkeypatch.setattr("builtins.input", lambda _prompt: "1")

    with pytest.raises(SystemExit, match="0"):
        resume(["--pam-state-dir", str(state_dir)])

    assert commands == [
        [
            "/bin/codex",
            "-C",
            str(workspace),
            "resume",
            "--include-non-interactive",
            "discord-conversation",
        ]
    ]
