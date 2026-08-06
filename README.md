# pam (personal-agent-manager)

Pam turns Discord into a remote interface for Codex on your own computer. Approve the directory
that contains your projects once, then start or continue conversations by text or voice in either
the terminal or Discord.

```text
approved directory       → projects Pam may discover
central Pam server       → one Forum channel per project
Codex session            → one live Forum post
```

Pam also keeps portable conversation records on your computer in human-readable Markdown and
machine-readable JSONL.

## Install

### 1. Install Pam and authenticate Codex

On the computer where Codex and your projects live:

```bash
git clone https://github.com/jlaffy/pam-discord.git
cd pam-discord
./install.sh
codex login
```

You can give these commands and this README to a Codex agent and ask it to guide the setup. The
agent can install and verify Pam, but you must personally create the Discord bot, copy its secret
token, and authorize it in your server.

### 2. Create a Discord bot

- Open the [Discord Developer Portal](https://discord.com/developers/applications) and click
  **New Application**.
- Name it `pam` and click **Create**.
- Open **Bot** and enable **Message Content Intent**.
- Under **Token**, click **Reset Token**, then **Copy**. Keep this token private.

### 3. Copy your Discord user ID

- In Discord, open **User Settings → Advanced** and enable **Developer Mode**.
- Right-click your own name or picture and click **Copy User ID**.

### 4. Run the guided setup

```bash
pam setup
```

Paste the user ID and bot token when prompted. Choose the parent directory containing the projects
you want Pam to discover. Follow the displayed links to create one Discord server named `pam` and
add the bot. Pam writes a private configuration, starts its background service, and discovers
Codex sessions under the approved directory.

### 5. Use Pam

Use Codex normally anywhere beneath the approved directory. Sessions appear in the corresponding
project Forum automatically, normally within five seconds. You can also create a new post in a
project Forum to start the matching Codex session from Discord.

```text
#recent-sessions          → newest sessions across all projects
project Forum             → complete session list for one project
Forum post                → one shared Discord/Codex conversation
```

## How it behaves

Pam stays running after you disconnect. It mirrors prompts, responses, voice transcripts, tool
events, names, and activity metadata while keeping Codex session data canonical on disk. Use
`pam resume` to browse conversations that started in either the terminal or Discord.

Discord-started Codex work has full local access by default, equivalent to `codex --yolo`. It has
the same filesystem, network, and account permissions as the Unix user running Pam. Set
`codex_full_access = false` in Pam's private `config.toml` to use Codex's normal sandbox.

When Codex links to a supported project file, Pam uploads it to the Discord conversation. Pam's
built-in Discord instruction makes requested images and other deliverables appear as attachments
without requiring a reminder in every conversation. Files above the Discord server's upload limit
remain on the computer and Pam reports their path.

## Deployment modes

Fresh installations use `mode = "central"`: one Pam server automatically discovers projects under
approved roots. `mode = "dedicated"` gives selected projects their own servers. `mode = "hybrid"`
supports configurations containing both a central
destination and selected dedicated project servers. Codex history remains canonical on disk, so
changing destinations later does not require physically moving old Discord threads.

See [the central-server design and future hybrid routing](docs/always-on-pam.md) and
[recommended optional setup](docs/recommended-setup.md).

## Help

```bash
pam doctor            # check configuration, Discord, and Codex
pam resume            # browse conversations for the current project
pam service status    # check whether Pam is running
pam service logs      # show recent activity and errors
pam service restart   # restart Pam
```

Questions, feedback, or ideas? Use
[GitHub Discussions](https://github.com/jlaffy/pam-discord/discussions).

MIT licensed.
