from __future__ import annotations

import argparse
import json
import os
import signal
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .setup import DEFAULT_STATE_DIR, doctor

SERVICE_NAME = "pam-discord.service"


def _systemctl(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    binary = shutil.which("systemctl")
    if binary is None:
        raise SystemExit(
            "Background service installation currently requires Linux with systemd. "
            "Run `./pam run` directly on other systems."
        )
    return subprocess.run(
        [binary, "--user", *args],
        check=check,
        text=True,
    )


def _user_systemd_available() -> bool:
    binary = shutil.which("systemctl")
    if binary is None:
        return False
    result = subprocess.run(
        [binary, "--user", "show-environment"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def _fallback_metadata(state_dir: Path) -> Path:
    return state_dir / "background-service.json"


def _fallback_running(state_dir: Path) -> bool:
    path = _fallback_metadata(state_dir)
    if not path.exists():
        return False
    pid = int(json.loads(path.read_text(encoding="utf-8"))["pid"])
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    command_path = Path(f"/proc/{pid}/cmdline")
    if command_path.exists():
        command = command_path.read_bytes().replace(b"\0", b" ").decode(errors="replace")
        return "pam-discord" in command and str(state_dir / "config.toml") in command
    return True


def _fallback_start(state_dir: Path, executable: Path | None = None) -> None:
    if _fallback_running(state_dir):
        return
    metadata_path = _fallback_metadata(state_dir)
    previous = (
        json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata_path.exists()
        else {}
    )
    executable = executable or Path(str(previous.get("executable", sys.argv[0]))).resolve()
    log_path = state_dir / "pam.log"
    log = log_path.open("a", encoding="utf-8")
    process = subprocess.Popen(
        [
            str(executable),
            "run",
            "--env-file",
            str(state_dir / ".env"),
            "--config",
            str(state_dir / "config.toml"),
        ],
        cwd=str(previous.get("working_dir", Path.cwd())),
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    metadata_path.write_text(
        json.dumps(
            {
                "pid": process.pid,
                "executable": str(executable),
                "working_dir": str(Path.cwd().resolve()),
                "log": str(log_path),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    metadata_path.chmod(0o600)


def _fallback_stop(state_dir: Path) -> None:
    path = _fallback_metadata(state_dir)
    if not _fallback_running(state_dir):
        return
    pid = int(json.loads(path.read_text(encoding="utf-8"))["pid"])
    os.kill(pid, signal.SIGTERM)
    for _ in range(50):
        if not _fallback_running(state_dir):
            break
        time.sleep(0.1)


def _unit_quote(value: str) -> str:
    escaped = value.replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _service_path() -> Path:
    config_root = Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser()
    return config_root / "systemd" / "user" / SERVICE_NAME


def _service_content(state_dir: Path, executable: Path, working_dir: Path) -> str:
    env_path = state_dir / ".env"
    config_path = state_dir / "config.toml"
    path_value = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    command = " ".join(
        _unit_quote(str(value))
        for value in (
            executable,
            "run",
            "--env-file",
            env_path,
            "--config",
            config_path,
        )
    )
    return f"""[Unit]
Description=Pam Discord agent bridge
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={_unit_quote(str(working_dir))}
Environment={_unit_quote(f"PATH={path_value}")}
ExecStart={command}
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
UMask=0077

[Install]
WantedBy=default.target
"""


def _lingering_enabled() -> bool | None:
    loginctl = shutil.which("loginctl")
    if loginctl is None:
        return None
    result = subprocess.run(
        [loginctl, "show-user", str(os.getuid()), "--property=Linger", "--value"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip().lower() == "yes"


def install(state_dir: Path, *, force: bool = False) -> None:
    state_dir = state_dir.expanduser().resolve()
    doctor(["--state-dir", str(state_dir)])
    executable = Path(sys.argv[0]).resolve()
    working_dir = Path.cwd().resolve()
    if not _user_systemd_available():
        _fallback_start(state_dir, executable)
        print("Pam is running in the background (systemd is unavailable on this machine).")
        print(f"Logs: {state_dir / 'pam.log'}")
        return
    path = _service_path()
    content = _service_content(state_dir, executable, working_dir)
    if path.exists() and not force:
        if path.read_text(encoding="utf-8") == content:
            print(f"Pam service is already installed at {path}")
        else:
            raise SystemExit(
                f"A different Pam service already exists at {path}. "
                "Re-run with `./pam service install --force` to replace it."
            )
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        path.chmod(0o600)
        print(f"Installed Pam service: {path}")

    _systemctl("daemon-reload")
    _systemctl("enable", "--now", SERVICE_NAME)
    print("Pam is running in the background and will restart after failures.")
    print("Check it with: ./pam service status")
    print("Read its logs with: ./pam service logs")

    lingering = _lingering_enabled()
    if lingering is False:
        print(
            "\nOne server setting remains: enable user lingering so Pam stays online after "
            "you log out and starts at boot:"
        )
        print(f"  loginctl enable-linger {getpass_user()}")
    elif lingering is True:
        print("User lingering is enabled; Pam can remain online after logout and start at boot.")


def getpass_user() -> str:
    import getpass

    return getpass.getuser()


def status(state_dir: Path = DEFAULT_STATE_DIR) -> None:
    state_dir = state_dir.expanduser().resolve()
    if not _user_systemd_available():
        print("Pam is running." if _fallback_running(state_dir) else "Pam is not running.")
        if not _fallback_running(state_dir):
            raise SystemExit(1)
        return
    result = _systemctl("status", SERVICE_NAME, "--no-pager", check=False)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def restart(state_dir: Path = DEFAULT_STATE_DIR) -> None:
    state_dir = state_dir.expanduser().resolve()
    if not _user_systemd_available():
        _fallback_stop(state_dir)
        _fallback_start(state_dir)
        print("Pam restarted.")
        return
    _systemctl("restart", SERVICE_NAME)
    print("Pam restarted.")


def stop(state_dir: Path = DEFAULT_STATE_DIR) -> None:
    state_dir = state_dir.expanduser().resolve()
    if not _user_systemd_available():
        _fallback_stop(state_dir)
        print("Pam stopped.")
        return
    _systemctl("stop", SERVICE_NAME)
    print("Pam stopped. It remains enabled for the next login or boot.")


def start(state_dir: Path = DEFAULT_STATE_DIR) -> None:
    state_dir = state_dir.expanduser().resolve()
    if not _user_systemd_available():
        _fallback_start(state_dir)
        print("Pam started.")
        return
    _systemctl("start", SERVICE_NAME)
    print("Pam started.")


def logs(lines: int, state_dir: Path = DEFAULT_STATE_DIR) -> None:
    state_dir = state_dir.expanduser().resolve()
    if not _user_systemd_available():
        path = state_dir / "pam.log"
        content = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        print("\n".join(content[-lines:]))
        return
    journalctl = shutil.which("journalctl")
    if journalctl is None:
        raise SystemExit("journalctl was not found")
    result = subprocess.run(
        [journalctl, "--user", "--unit", SERVICE_NAME, "--lines", str(lines), "--no-pager"],
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def uninstall(state_dir: Path = DEFAULT_STATE_DIR) -> None:
    state_dir = state_dir.expanduser().resolve()
    if not _user_systemd_available():
        _fallback_stop(state_dir)
        _fallback_metadata(state_dir).unlink(missing_ok=True)
        print("Pam background service removed. Pam configuration and archives were kept.")
        return
    path = _service_path()
    _systemctl("disable", "--now", SERVICE_NAME, check=False)
    if path.exists():
        path.unlink()
    _systemctl("daemon-reload")
    print("Pam background service removed. Pam configuration and archives were kept.")


def service(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Keep Pam running in the background")
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    commands = parser.add_subparsers(dest="command", required=True)
    install_parser = commands.add_parser("install", help="Install and start the service")
    install_parser.add_argument("--force", action="store_true")
    commands.add_parser("status", help="Show whether Pam is running")
    commands.add_parser("start", help="Start Pam")
    commands.add_parser("stop", help="Stop Pam until next login or boot")
    commands.add_parser("restart", help="Restart Pam")
    logs_parser = commands.add_parser("logs", help="Show recent Pam logs")
    logs_parser.add_argument("--lines", type=int, default=100)
    commands.add_parser("uninstall", help="Remove the service but keep Pam data")
    args = parser.parse_args(argv)

    if args.command == "install":
        install(args.state_dir, force=args.force)
    elif args.command == "status":
        status(args.state_dir)
    elif args.command == "start":
        start(args.state_dir)
    elif args.command == "stop":
        stop(args.state_dir)
    elif args.command == "restart":
        restart(args.state_dir)
    elif args.command == "logs":
        if args.lines < 1:
            raise SystemExit("--lines must be positive")
        logs(args.lines, args.state_dir)
    elif args.command == "uninstall":
        uninstall(args.state_dir)
