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
shared conversations     → start or continue in the terminal or Discord
complete history         → save prompts and conversations in Markdown and JSONL
persistent remote access → reach your remote computer and Codex through Discord
fast voice               → transcribe voice notes using the fastest reliable CPU or GPU mode detected
file delivery            → receive and easily view generated files in Discord
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

Paste the Discord user ID and bot token from Steps 2–3. When asked:

> **Where should pam be allowed to connect or create project directories?**

choose the directory containing your projects. pam suggests the parent of the cloned `pam-discord`
repository. Follow the two links to create a Discord server named `pam` and add the bot.

```text
general pam server → general conversations and project management
project server     → one project directory and its project-specific work
```

### 5. Connect or create projects

From the general `pam` server—or the terminal—connect an existing project or create a new one:

```text
pam project connect /path/to/existing-project
pam project create /path/to/new-project
```

Follow the two links; pam detects the new project server and finishes configuration automatically.
When connecting an existing project, choose whether `.pam/` conversation history is ignored by Git
or can be committed.

## Done

```text
project directory                 ↔ Discord server
project subdirectory              ↔ Discord channel
Codex session in that directory   ↔ Discord thread in the corresponding channel
conversation                      = the same linked history viewed through either interface
```

We use *conversation* as the general term for what appears as a Codex session in the terminal and a
Discord thread in Discord.

Use Codex normally inside a connected project. pam automatically mirrors active conversations into
Discord, using `#general` for the project root and creating channels for subdirectories when needed.
Use `pam resume` to browse conversations that started in either the terminal or Discord.

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
