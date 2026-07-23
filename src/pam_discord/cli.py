from __future__ import annotations

import sys

from . import bot
from .service import service
from .shared_cli import codex, link, resume
from .setup import DEFAULT_STATE_DIR, doctor, project_add, setup


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] == "setup":
        setup(args[1:])
        return
    if args and args[0] == "doctor":
        doctor(args[1:])
        return
    if len(args) >= 2 and args[0] == "project" and args[1] == "add":
        project_add(args[2:])
        return
    if len(args) >= 2 and args[0] == "project" and args[1] == "connect":
        project_add(args[2:])
        return
    if len(args) >= 2 and args[0] == "project" and args[1] == "create":
        project_add(args[2:], create=True)
        return
    if len(args) >= 2 and args[0] == "hub" and args[1] == "create":
        project_add(args[2:], hub=True)
        return
    if args and args[0] == "service":
        service(args[1:])
        return
    if args and args[0] == "codex":
        codex(args[1:])
        return
    if args and args[0] == "link":
        link(args[1:])
        return
    if args and args[0] == "resume":
        resume(args[1:])
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
            "pam connects Discord to Codex sessions on this remote computer.\n\n"
            "Commands:\n"
            "  pam setup             Save your Discord identity and bot token once\n"
            "  pam hub create PATH   Create the general pam server\n"
            "  pam project connect PATH  Connect an existing project\n"
            "  pam project add PATH  Alias for project connect\n"
            "  pam project create PATH  Create and connect a new project\n"
            "  pam codex [OPTIONS]   Start a terminal and Discord shared session\n"
            "  pam resume [OPTIONS]  Resume any project session, including Discord starts\n"
            "  pam link              Link the latest Codex conversation in this directory\n"
            "  pam doctor            Check configuration, Discord, and Codex\n"
            "  pam service           Manage the background service\n"
            "  pam run               Run pam in the foreground\n\n"
            "Existing usage without 'run' remains supported."
        )
        return
    bot.main(args)
