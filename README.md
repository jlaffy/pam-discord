# Pam

Talk or type to Codex from Discord. Pam runs Codex on your remote computer.

## Start here

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
```

Follow the terminal instructions. Pam gives you links to:

1. Create the new project server.
2. Add Pam to that server.

Finally, open the channel link Pam prints and send a message.

## Done

```text
Discord server = your project
channel        = project conversation area
thread         = persistent Codex session
voice message  = saved audio + transcript + Codex request
```

Pam stays running after you disconnect. Conversation history is saved in
`<project>/.pam/conversations/` and ignored by Git.

## Help

```bash
./pam doctor
./pam service status
./pam service logs
./pam service restart
```

MIT licensed.
