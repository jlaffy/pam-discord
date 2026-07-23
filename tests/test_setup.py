from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import pytest

from pam_discord.config import load_config
from pam_discord.setup import (
    DISCORD_BOT_PERMISSIONS,
    _check_github_cli,
    _discord_install_url,
    _prepare_discord_workspace,
    doctor,
    project_add,
    setup,
    _whisper_defaults,
)


def test_whisper_defaults_fall_back_to_fast_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ctranslate2.get_cuda_device_count", lambda: 0)

    assert _whisper_defaults() == ("tiny.en", "cpu", "int8")


def test_github_cli_check_reports_authenticated_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("pam_discord.setup.shutil.which", lambda _name: "/bin/gh")
    results = iter(
        [
            subprocess.CompletedProcess([], 0, stdout="", stderr=""),
            subprocess.CompletedProcess([], 0, stdout="octocat\n", stderr=""),
        ]
    )
    monkeypatch.setattr("pam_discord.setup.subprocess.run", lambda *_args, **_kwargs: next(results))

    assert _check_github_cli() == (True, "authenticated as octocat")


def test_setup_creates_private_single_project_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_dir = tmp_path / "pam-state"
    workspace = tmp_path / "project"
    workspace.mkdir()
    monkeypatch.setattr("pam_discord.setup.getpass.getpass", lambda _: "private-token")
    monkeypatch.setattr(
        "pam_discord.setup._prepare_discord_workspace",
        lambda *_args, **_kwargs: (222, 333, "https://discord.com/channels/333/222"),
    )

    setup(
        [
            "--state-dir",
            str(state_dir),
            "--workspace",
            str(workspace),
            "--user-id",
            "111",
            "--channel-id",
            "222",
            "--no-service",
            "--ignore-history",
        ]
    )

    config = load_config(state_dir / "config.toml")
    assert config.allowed_user_ids == frozenset({111})
    assert config.channels[222].workspace == workspace
    assert config.channels[222].run_codex is True
    assert config.channels[222].project_record_dir == workspace / ".pam" / "conversations"
    assert config.guilds[333].workspace == workspace
    assert config.guilds[333].project_record_dir == workspace / ".pam" / "conversations"
    assert config.whisper_beam_size == 1
    assert (workspace / ".gitignore").read_text() == ".pam/\n"
    assert (state_dir / ".env").read_text() == "DISCORD_BOT_TOKEN=private-token\n"
    assert stat.S_IMODE((state_dir / ".env").stat().st_mode) == 0o600
    assert stat.S_IMODE((state_dir / "config.toml").stat().st_mode) == 0o600


def test_setup_preserves_gitignore_and_adds_pam_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    ignore = workspace / ".gitignore"
    ignore.write_text("data/\n")
    monkeypatch.setattr("pam_discord.setup.getpass.getpass", lambda _: "private-token")
    monkeypatch.setattr(
        "pam_discord.setup._prepare_discord_workspace",
        lambda *_args, **_kwargs: (222, 333, "https://discord.com/channels/333/222"),
    )

    setup(
        [
            str(workspace),
            "--state-dir",
            str(tmp_path / "pam-state"),
            "--user-id",
            "111",
            "--no-service",
            "--ignore-history",
        ]
    )

    assert ignore.read_text() == "data/\n.pam/\n"


def test_setup_does_not_overwrite_existing_private_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_dir = tmp_path / "pam-state"
    workspace = tmp_path / "project"
    state_dir.mkdir()
    workspace.mkdir()
    env_path = state_dir / ".env"
    env_path.write_text("keep-me\n")
    monkeypatch.setattr("pam_discord.setup.getpass.getpass", lambda _: "new-token")

    with pytest.raises(SystemExit, match="without overwriting"):
        setup(
            [
                "--state-dir",
                str(state_dir),
                "--workspace",
                str(workspace),
                "--user-id",
                "111",
                "--channel-id",
                "222",
                "--no-service",
            ]
        )

    assert env_path.read_text() == "keep-me\n"


def test_doctor_checks_generated_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_dir = tmp_path / "pam-state"
    workspace = tmp_path / "project"
    workspace.mkdir()
    monkeypatch.setattr("pam_discord.setup.getpass.getpass", lambda _: "private-token")
    monkeypatch.setattr(
        "pam_discord.setup._prepare_discord_workspace",
        lambda *_args, **_kwargs: (222, 333, "https://discord.com/channels/333/222"),
    )
    setup(
        [
            "--state-dir",
            str(state_dir),
            "--workspace",
            str(workspace),
            "--user-id",
            "111",
            "--channel-id",
            "222",
            "--no-service",
            "--ignore-history",
        ]
    )
    monkeypatch.setattr(
        "pam_discord.setup._check_codex", lambda _: (True, "Logged in using ChatGPT")
    )
    monkeypatch.setattr(
        "pam_discord.setup._check_discord",
        lambda _token, channels, guilds: (
            True,
            f"connected; {len(guilds)} server(s), {len(channels)} channel(s)",
        ),
    )

    doctor(["--state-dir", str(state_dir)])


def test_discord_install_url_requests_required_bot_permissions() -> None:
    url = _discord_install_url("999")

    assert "client_id=999" in url
    assert "scope=bot" in url
    assert f"permissions={DISCORD_BOT_PERMISSIONS}" in url


def test_prepare_discord_workspace_uses_general_in_dedicated_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "great-project"
    workspace.mkdir()
    calls: list[tuple[str, str, object]] = []
    monkeypatch.setattr(
        "pam_discord.setup._discord_get",
        lambda _token, _path: {"id": "999", "username": "pam"},
    )

    def request(_token: str, path: str, *, method: str = "GET", payload=None):
        calls.append((path, method, payload))
        if path == "/users/@me/guilds":
            return [{"id": "333", "name": "Research"}]
        if path == "/guilds/333/members/@me" and method == "PATCH":
            return {"nick": "pam"}
        if path == "/guilds/333/channels" and method == "GET":
            return [{"id": "555", "name": "general", "type": 0}]
        raise AssertionError((path, method, payload))

    monkeypatch.setattr("pam_discord.setup._discord_request", request)

    result = _prepare_discord_workspace(
        "token", workspace, channel_id=None, guild_id=333, channel_name=None
    )

    assert result == (555, 333, "https://discord.com/channels/333/555")
    assert calls[1] == ("/guilds/333/members/@me", "PATCH", {"nick": "pam"})
    assert calls[2] == ("/guilds/333/channels", "GET", None)


def test_setup_installs_service_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_dir = tmp_path / "pam-state"
    workspace = tmp_path / "project"
    workspace.mkdir()
    installed: list[Path] = []
    monkeypatch.setattr("pam_discord.setup.getpass.getpass", lambda _: "private-token")
    monkeypatch.setattr(
        "pam_discord.setup._prepare_discord_workspace",
        lambda *_args, **_kwargs: (222, 333, "https://discord.com/channels/333/222"),
    )
    monkeypatch.setattr("pam_discord.service.install", installed.append)

    setup(
        [
            str(workspace),
            "--state-dir",
            str(state_dir),
            "--user-id",
            "111",
            "--ignore-history",
        ]
    )

    assert installed == [state_dir.resolve()]


def test_setup_can_make_conversation_history_trackable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    ignore = workspace / ".gitignore"
    ignore.write_text("data/\n.pam/\n")
    monkeypatch.setattr("pam_discord.setup.getpass.getpass", lambda _: "private-token")
    monkeypatch.setattr(
        "pam_discord.setup._prepare_discord_workspace",
        lambda *_args, **_kwargs: (222, 333, "https://discord.com/channels/333/222"),
    )

    setup(
        [
            str(workspace),
            "--state-dir",
            str(tmp_path / "pam-state"),
            "--user-id",
            "111",
            "--track-history",
            "--no-service",
        ]
    )

    assert ignore.read_text() == "data/\n"


def test_identity_setup_once_then_add_multiple_projects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_dir = tmp_path / "pam-state"
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    monkeypatch.setattr("pam_discord.setup.getpass.getpass", lambda _: "private-token")
    created = iter(
        [
            (222, 333, "https://discord.com/channels/333/222"),
            (444, 555, "https://discord.com/channels/555/444"),
        ]
    )
    monkeypatch.setattr(
        "pam_discord.setup._prepare_discord_workspace", lambda *_args, **_kwargs: next(created)
    )

    setup(["--state-dir", str(state_dir), "--user-id", "111"])
    project_add(
        [str(first), "--state-dir", str(state_dir), "--ignore-history", "--no-service"]
    )
    project_add(
        [str(second), "--state-dir", str(state_dir), "--ignore-history", "--no-service"]
    )

    config = load_config(state_dir / "config.toml")
    assert set(config.guilds) == {333, 555}
    assert config.guilds[333].workspace == first
    assert config.guilds[555].workspace == second
    assert (state_dir / ".env").read_text() == "DISCORD_BOT_TOKEN=private-token\n"
