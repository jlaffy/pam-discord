# pam

**pam** means **personal-agent-manager**.

pam is a self-hosted bridge that runs on the same remote computer as your projects and Codex,
turning each Discord server into a remote interface for one project directory.

Talk or type to Codex from Discord.

pam enables shared Codex sessions between the Terminal and Discord, so you can continue the same
Codex conversation from your phone.

[Learn how pam, Discord, Codex, and your project directories fit
together](docs/how-pam-works.md).

```text
remote access       → reach your remote server and Codex through Discord on your phone or computer
complete records    → prompts and conversation transcripts saved from audio or text
shared sessions     → continue in the terminal or Discord
file delivery       → receive generated plots, presentations, documents, and tables in Discord
fast voice          → transcribe voice notes using the fastest reliable CPU or GPU mode detected
```

## Start here

> [!NOTE]
> Steps 1–4 happen once. Repeat Step 5 for every project you connect.

### 1. Install

Open a terminal on the remote computer. Paste:

```bash
git clone https://github.com/jlaffy/pam-discord.git
cd pam-discord
./install.sh
codex login
```

### 2. Make the Discord bot

- Open the [Discord Developer Portal](https://discord.com/developers/applications) and click
  **New Application**.
- Name it `pam` and click **Create**.
- Click **Bot** on the left.
- Turn on **Message Content Intent**.
- Under **Token**, click **Reset Token**, then **Copy**. Do not share this token.

### 3. Copy your Discord user ID

- Open Discord and go to **User Settings → Advanced**.
- Turn on **Developer Mode**.
- Right-click your own name or picture and click **Copy User ID**.

### 4. Finish pam setup

```bash
pam setup
```

Paste the Discord user ID and bot token from Steps 2–3.

### 5. Connect a project

Replace the example path with your project directory:

```bash
pam project add /ewsc/jlaffy/agent-native-genomics
```

Choose whether `.pam/` conversation history is ignored by Git or can be committed.

Follow the terminal instructions. pam gives you links to:

1. Create a new Discord server for the project.
2. Add pam to the new Discord server.

Open the Discord link pam prints and send your first message.

### 6. Use terminal and Discord shared sessions

| Start a new linked conversation | Link one already in progress |
| --- | --- |
| `pam codex --yolo` | Ask Codex: `Link this conversation to pam` |

Both options create a Discord thread, preserve existing history, and continue the same session from
the terminal or Discord. Normal Codex options still work—for example, `pam codex --yolo`.

## Done

```text
Discord server = your project
channel        = project conversation area
thread         = persistent Codex session
conversation   = saved prompts and transcripts from audio or text, plus replies
```

pam stays running after you disconnect. Conversation history is saved in
`<project>/.pam/conversations/`. During setup, you choose whether Git ignores or tracks it.

pam runs Discord-started Codex work with full local access by default, equivalent to
`codex --yolo`. It has the same filesystem, network, and account permissions as the Unix user
running pam. Set `codex_full_access = false` in pam's `config.toml` to use Codex's normal sandbox.
When Codex links to a supported file inside the project, pam uploads it to the conversation thread.
Files over the Discord server's upload limit stay on the remote machine and pam reports their path.

If you also want to speak prompts on your Mac, you can optionally enable
[macOS Dictation](docs/macos-dictation.md).

## Help

```bash
pam doctor            # check Discord, Codex, and project setup
pam service status    # check whether pam is running
pam service logs      # show recent activity and errors
pam service restart   # restart pam
```

MIT licensed.
