# pam (personal-agent-manager)

pam turns Discord into a remote interface for Codex on your own computer. Connect a project
directory once, then start or continue conversations by text or voice, in the terminal or in
Discord: **project directories** become **Discord servers**, **subdirectories** become **channels**
(where relevant), and **Codex sessions** become **threads**.

pam also keeps a complete, portable history of your prompts and conversations on your computer, in
human-readable Markdown and machine-readable JSONL.

[Learn how pam, Discord, Codex, and your project directories fit
together](docs/how-pam-works.md).

```text
remote access → reach your remote computer and Codex through Discord
fast voice    → transcribe voice notes using the fastest reliable CPU or GPU mode detected
file delivery → receive generated plots, presentations, documents, and tables in Discord
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

After connecting the first project, you can add another from Discord by sending:

```text
pam project add /path/to/project
```

Follow the two links pam provides. pam detects the new Discord server and finishes configuration
automatically.

To create a new project directory and connect it in one step, use this command in Discord or the
terminal:

```text
pam project create /path/to/new-project
```

### 6. Use the same conversations in the terminal and Discord

Start a terminal conversation with:

```bash
pam codex --yolo
```

You can also use Codex normally inside a connected project. pam automatically discovers active
conversations and mirrors them into Discord. Root conversations appear in `#general`; conversations
started in subdirectories appear in channels created for those directories.

Run `pam resume` inside a connected project to browse all its conversations, including ones that
started in Discord. Use `pam resume` rather than `codex resume`: Codex normally hides
non-interactive Discord starts, while pam intentionally presents both origins in one list.

## Done

```text
project directory                 ↔ Discord server
project subdirectory              ↔ Discord channel
Codex session in that directory   ↔ Discord thread in the corresponding channel
conversation                      = the same linked history viewed through either interface
```

We use *conversation* as the general term for what appears as a Codex session in the terminal and a
Discord thread in Discord.

pam stays running after you disconnect. It saves a complete, portable record of your work with
Codex on your own computer—human-readable in Markdown and machine-readable in JSONL—including
prompts, responses, voice transcripts, and agent events. Project history lives in
`<project>/.pam/conversations/`; during setup, you choose whether Git ignores or tracks it.

pam runs Discord-started Codex work with full local access by default, equivalent to
`codex --yolo`. It has the same filesystem, network, and account permissions as the Unix user
running pam. Set `codex_full_access = false` in pam's `config.toml` to use Codex's normal sandbox.
When Codex links to a supported file inside the project, pam uploads it to the conversation thread.
Files over the Discord server's upload limit stay on the remote machine and pam reports their path.

If you also want to speak prompts on your Mac, you can optionally enable
[macOS Dictation](docs/macos-dictation.md).

Questions, feedback, or ideas? Join the
[pam discussions](https://github.com/jlaffy/pam-discord/discussions).

To keep project servers together in Discord, drag one PAM server onto another, name the resulting
folder `pam`, and choose a folder color.

See [recommended optional setup](docs/recommended-setup.md) for local developer tools, Codex
permissions, and macOS Dictation.

## Help

```bash
pam doctor            # check Discord, Codex, and project setup
pam resume            # browse every conversation for the current project
pam service status    # check whether pam is running
pam service logs      # show recent activity and errors
pam service restart   # restart pam
```

MIT licensed.
