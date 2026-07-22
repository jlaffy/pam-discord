from __future__ import annotations

import sys

from . import bot
from .setup import DEFAULT_STATE_DIR, doctor, setup


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] == "setup":
        setup(args[1:])
        return
    if args and args[0] == "doctor":
        doctor(args[1:])
        return
    if args and args[0] == "run":
        run_args = args[1:]
        if not run_args:
            state_dir = DEFAULT_STATE_DIR.expanduser()
            run_args = [
                "--env-file",
                str(state_dir / ".env"),
                "--config",
                str(state_dir / "config.toml"),
            ]
        bot.main(run_args)
        return
    if args and args[0] in {"-h", "--help"}:
        print(
            "Pam connects a Discord channel to a local project and Codex session.\n\n"
            "Commands:\n"
            "  pam-discord setup   Guided first-time setup\n"
            "  pam-discord doctor  Check configuration, Discord secret, and Codex\n"
            "  pam-discord run     Start Pam\n\n"
            "Existing usage without 'run' remains supported."
        )
        return
    bot.main(args)
