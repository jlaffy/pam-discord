# Pam product decisions

This file records agreed product behavior.

## Purpose

> Talk or type to Codex from Discord. Pam runs Codex on your remote computer.

Pam keeps people connected to persistent agent sessions on remote machines. Codex is the first
supported agent. Transcription and logging support that purpose; they are not the product itself.

Pam's three core benefits are always-on access from Discord on a phone or computer to Codex and
work on a remote server, complete project-specific conversation records, and terminal and Discord
shared sessions.

## Mental model

```text
one Pam installation + Discord bot = one person
one project directory              = one project-specific Discord server
every server channel               = connected to the project automatically
every Discord thread               = one persistent agent session
```

Pam setup and project setup are separate:

```text
Once:
install Pam
connect Pam to your Discord account/bot

For every project:
choose project directory
create project Discord server
add your existing Pam bot
```

## Conversation records

Pam saves the complete conversation: typed messages, original audio, visible transcripts, exact
agent prompts, replies, events, session IDs, authors, timestamps, and whether each item came from
Discord or a terminal. Records live under `<project>/.pam/conversations/`.

Setup asks whether `.pam/` should be ignored by Git or available to commit. Ignoring is the default;
Pam never uploads conversation history automatically.

## Direct Codex sessions

```bash
pam codex [normal Codex options]
```

This starts Codex normally, creates a thread in the project's `#general` channel, mirrors the full
conversation, and allows the same session to continue from either the terminal or Discord.
Options such as `--yolo` are optional Codex options that Pam passes through unchanged.

If the directory has no Pam project server yet, the command:

1. Gives the user a link to create a new Discord server for the project.
2. Gives the user a link to add their existing Pam bot.
3. Connects `#general`, creates the first session thread, and starts Codex.

Later runs open immediately. An already-running Codex session can be linked by asking Codex to
"Link this conversation to Pam" or by running `pam link`. Pam imports its existing history before
continuing live in Discord.

Pam launches the real Codex command; it does not replace or shadow the `codex` executable. The
shared-session connection should use Codex's app-server rather than pretending that two separate
Codex processes are one live session.
