from __future__ import annotations

import stat
from pathlib import Path

import pytest

from pam_discord.config import load_config
from pam_discord.setup import doctor, setup


def test_setup_creates_private_single_project_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_dir = tmp_path / "pam-state"
    workspace = tmp_path / "project"
    workspace.mkdir()
    monkeypatch.setattr("pam_discord.setup.getpass.getpass", lambda _: "private-token")

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
        ]
    )

    config = load_config(state_dir / "config.toml")
    assert config.allowed_user_ids == frozenset({111})
    assert config.channels[222].workspace == workspace
    assert config.channels[222].run_codex is True
    assert (state_dir / ".env").read_text() == "DISCORD_BOT_TOKEN=private-token\n"
    assert stat.S_IMODE((state_dir / ".env").stat().st_mode) == 0o600
    assert stat.S_IMODE((state_dir / "config.toml").stat().st_mode) == 0o600


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
        ]
    )
    monkeypatch.setattr(
        "pam_discord.setup._check_codex", lambda _: (True, "Logged in using ChatGPT")
    )
    monkeypatch.setattr(
        "pam_discord.setup._check_discord",
        lambda _token, channels: (True, f"connected; {len(channels)} channel(s) accessible"),
    )

    doctor(["--state-dir", str(state_dir)])
