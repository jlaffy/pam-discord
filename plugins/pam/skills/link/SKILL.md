---
name: link
description: >-
  Link the current or most recent Codex CLI conversation to the project's pam Discord server when
  the user asks to link, share, or continue this conversation in pam or Discord.
---

Run this command from the current project directory:

```bash
pam link --cwd "$PWD"
```

Report the resulting linked Codex conversation ID. pam imports its prior history, creates a Discord
thread in the project's server, and continues mirroring new messages between the terminal and
Discord. If the command says pam is not running or the project is not connected, report that exact
instruction to the user.
