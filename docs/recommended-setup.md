# Recommended optional setup

pam works after the normal installation and `pam setup`. The following additions are optional.

## Authenticate local developer tools

pam and Codex run as your Unix user, so they can use developer tools you have already authenticated
on that remote computer. Let each tool manage its own credentials rather than copying tokens into
pam.

For GitHub, install the [GitHub CLI](https://cli.github.com/) and run:

```bash
gh auth login
gh auth status
```

`pam setup` detects an installed GitHub CLI and can offer to start login. `pam doctor` reports its
status. GitHub remains optional.

Only authenticate services you want Codex to use, and review the permissions each service receives.

## Choose Codex access

Discord-started pam work uses full local access by default, equivalent to Codex's `--yolo` option.
This lets Codex use the same files, network, and authenticated tools as your Unix user.

For terminal-started conversations:

```bash
pam codex --yolo
```

The `--yolo` option is not required. Omit it to use your normal Codex sandbox and approval settings.
You can also set `codex_full_access = false` in pam's central `config.toml` to disable full access
for Discord-started work.

Use full access only on a computer and in projects you trust.

## Dictate prompts on a Mac

If you want to speak prompts instead of typing, you can optionally enable
[macOS Dictation](macos-dictation.md). This is a general macOS feature, not part of pam.
