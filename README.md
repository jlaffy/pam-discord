# Pam

Talk or type to Codex from Discord. Pam runs Codex on your remote computer.

Pam is being built for terminal and Discord shared sessions, so you can continue the same Codex
conversation from your phone.

```text
remote access       → reach your remote server and Codex through Discord on your phone or computer
complete records    → prompts and conversation transcripts saved from audio or text
shared sessions     → continue in the terminal or Discord
```

## Start here

> [!NOTE]
> Steps 1–3 happen once. Repeat Step 4 for every project you connect.

### 1. Install

Open a terminal on the remote computer. Paste:

```bash
git clone https://github.com/jlaffy/pam-discord.git
cd pam-discord
./install.sh
codex login
```

### 2. Make the Discord bot

- Open [Discord Developer Portal](https://discord.com/developers/applications).
- Click **New Application**.
- Name it `Pam`.
- Click **Create**.
- Click **Bot** on the left.
- Turn on **Message Content Intent**.
- Click **Reset Token**.
- Click **Copy**. Do not share this token.

### 3. Copy your Discord user ID

- Open Discord.
- Go to **User Settings → Advanced**.
- Turn on **Developer Mode**.
- Right-click your own name or picture.
- Click **Copy User ID**.

### 4. Connect your project

Replace the example path with your project directory:

```bash
./pam setup /ewsc/jlaffy/agent-native-genomics
```

Pam asks for:

```text
Discord user ID  → paste the ID from step 3
Discord bot token → paste the token from step 2
Git history       → choose whether `.pam/` is ignored or can be committed
```

Follow the terminal instructions. Pam gives you links to:

1. Create a new Discord server for the project.
2. Add Pam to the new Discord server.

Pam prints a Discord link to `#general`. Open it and send your first message.

### 5. Use terminal and Discord shared sessions

> [!IMPORTANT]
> Coming soon — this step is not implemented yet.

| Start a new linked conversation | Link one already in progress |
| --- | --- |
| `pam codex` | `/pam link` |

Both options create a Discord thread, preserve existing history, and continue the same session from
the terminal or Discord. Normal Codex options still work—for example, `pam codex --yolo`.

## Done

```text
Discord server = your project
channel        = project conversation area
thread         = persistent Codex session
conversation   = saved prompts and transcripts from audio or text, plus replies
```

Pam stays running after you disconnect. Conversation history is saved in
`<project>/.pam/conversations/`. During setup, you choose whether Git ignores or tracks it.

## Help

```bash
./pam doctor            # check Discord, Codex, and project setup
./pam service status    # check whether Pam is running
./pam service logs      # show recent activity and errors
./pam service restart   # restart Pam
```

MIT licensed.
