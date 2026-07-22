from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pam_discord.service import _fallback_running, install, stop, uninstall


def test_install_creates_and_enables_private_user_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    executable = tmp_path / "pam-discord"
    executable.write_text("")
    config_root = tmp_path / "config"
    calls: list[tuple[str, ...]] = []

    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_root))
    monkeypatch.setattr("pam_discord.service._user_systemd_available", lambda: True)
    monkeypatch.setattr("pam_discord.service.doctor", lambda _args: None)
    monkeypatch.setattr("pam_discord.service.sys.argv", [str(executable)])
    monkeypatch.setattr("pam_discord.service._lingering_enabled", lambda: True)

    def fake_systemctl(*args: str, **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr("pam_discord.service._systemctl", fake_systemctl)
    install(state_dir)

    unit = config_root / "systemd" / "user" / "pam-discord.service"
    content = unit.read_text()
    assert "Restart=on-failure" in content
    assert str(state_dir / ".env") in content
    assert str(state_dir / "config.toml") in content
    assert str(executable) in content
    assert unit.stat().st_mode & 0o777 == 0o600
    assert ("daemon-reload",) in calls
    assert ("enable", "--now", "pam-discord.service") in calls


def test_uninstall_removes_service_but_not_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_root = tmp_path / "config"
    unit = config_root / "systemd" / "user" / "pam-discord.service"
    unit.parent.mkdir(parents=True)
    unit.write_text("service")
    state_file = tmp_path / "state" / "config.toml"
    state_file.parent.mkdir()
    state_file.write_text("keep")

    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_root))
    monkeypatch.setattr("pam_discord.service._user_systemd_available", lambda: True)
    monkeypatch.setattr(
        "pam_discord.service._systemctl",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0),
    )

    uninstall()

    assert not unit.exists()
    assert state_file.read_text() == "keep"


def test_install_falls_back_to_detached_process_without_user_systemd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    executable = tmp_path / "pam-discord"
    executable.write_text("#!/bin/sh\nwhile true; do sleep 1; done\n")
    executable.chmod(0o700)
    monkeypatch.setattr("pam_discord.service.doctor", lambda _args: None)
    monkeypatch.setattr("pam_discord.service.sys.argv", [str(executable)])
    monkeypatch.setattr("pam_discord.service._user_systemd_available", lambda: False)

    install(state_dir)
    try:
        assert _fallback_running(state_dir)
    finally:
        stop(state_dir)
