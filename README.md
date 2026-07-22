# Pam Discord

Pam Discord lets you direct Codex work from Discord using voice or text, from a phone or computer.

Send a voice message or text from your phone, or send text or upload a recording from your computer. Pam preserves the input, transcribes recordings locally, runs the instruction as a Codex task inside the project mapped to that Discord channel, saves the result, and replies in Discord.

```text
voice or text instruction
  -> saved input and transcript (for voice)
  -> Codex acts in the selected project
  -> saved result and Discord reply
```

This creates a documented, trackable channel for prompting agents and reviewing what they did. Discord is the interface; the actual work happens in local project repositories.

Pam uses local `faster-whisper` transcription and a ChatGPT-authenticated Codex CLI. It does not require an OpenAI or Anthropic API key and never falls back to metered model APIs. Codex plan limits still apply.

## How it works

Pam is a small program that stays running on your computer or server. It connects to Discord using a free bot account and waits for recordings from approved users.

When a recording arrives:

1. Pam identifies the project from the Discord channel.
2. It downloads the recording and transcribes it locally.
3. A top-level prompt starts a Discord thread and a matching Codex session.
4. Pam adds the other authorized collaborators to the thread.
5. Codex reads the project, performs the requested work, and replies in the thread.
6. Voice or text follow-ups in that thread resume the same Codex session.
7. Pam saves the complete chronological conversation and its underlying files.

There is no scheduled polling and no Codex window left open. Pam waits for Discord events and launches `codex exec` only when work arrives.

```text
phone or computer                 always-on computer/server
Discord voice message  ------->  Pam -> local project -> Codex
Discord reply          <-------        saved archive <- result
```

## What to expect today

- Each mapped Discord channel points to one local project directory.
- Each top-level prompt starts a thread; follow-ups in that thread retain Codex context.
- Both voice recordings and ordinary text messages can prompt or continue work.
- Discord can serve as the project's control surface: prompts may direct research, file changes, analyses, validation, branches, commits, and review or submission workflows.
- Codex performs only actions allowed by its permissions and the project's own instructions. For example, one project may permit PR creation while another delegates submission to a separate service.
- Replies appear after transcription and the Codex task finish; long tasks can take time.
- Pam must be running and online to receive new work.
- Native Discord voice recording is mobile-only; desktop users upload an audio file.

## The simple model

A Pam server is just the computer where the `pam-discord` process stays running. That machine must
be able to reach Discord and the project directory. For the first version, one Discord channel maps
to one project directory and uses the Codex account already signed in on that machine.

Pam keeps its private configuration, Discord token, archives, and session mappings together in a
data directory that you choose. Those files are separate from the project repository.

```text
Discord project channel
  -> Pam running on this server
  -> approved project directory
  -> your signed-in Codex CLI
```

## Guided setup

You need Python 3.11+, a Discord server, an online computer or Linux server, and the [Codex CLI](https://developers.openai.com/codex/cli/) signed in with ChatGPT. Clone every project you want Pam to use onto that same machine.

### 1. Install Pam and sign in to Codex

```bash
git clone https://github.com/jlaffy/pam-discord.git
cd pam-discord
./install.sh
codex login
```

The installer checks Python, creates a private virtual environment inside the checkout, installs
Pam, and tells you the next command. The `./pam` helper means you do not need to activate the
environment manually. Windows users can follow the manual installation instructions below.

### 2. Create a Discord bot

In the [Discord Developer Portal](https://discord.com/developers/applications):

1. Create an application and add a bot.
2. Enable **Message Content Intent**.
3. Invite it with **View Channels**, **Read Message History**, **Send Messages**, **Create Public Threads**, and **Send Messages in Threads** permissions.
4. Copy its token. Keep the token private.

### 3. Let Pam configure your first workspace

```bash
./pam setup
```

The setup guide asks for:

- A private Pam data directory
- The project directory Pam may use
- Your Discord user ID
- The project's Discord channel ID
- The Discord bot token, entered without displaying it

Pam creates private `.env` and `config.toml` files without overwriting existing setup. Enable
Discord **Developer Mode** to copy user and channel IDs.

### 4. Check and run

The setup guide prints exact commands using your chosen data directory. They look like:

```bash
./pam doctor
./pam run
```

Send a harmless first message in the mapped channel, such as: “Read this project's README and
summarize it without changing files.” Pam creates a Discord thread for the task; follow-ups inside
that thread resume the same Codex session.

### 5. Keep Pam online

Use `./pam run` for the first interactive test so you can see errors directly. Once that works, on
a Linux server run:

```bash
./pam service install
```

Pam validates the setup again, installs a private user-level `systemd` service, starts it
immediately, enables automatic startup, and restarts it after failures. It also tells you if Linux
user lingering must be enabled so Pam remains online after you end the SSH session.

Common service commands are:

```bash
./pam service status
./pam service logs
./pam service restart
./pam service stop
./pam service start
```

`./pam service uninstall` removes only the background service. It keeps configuration, session
history, transcripts, and recordings.

## Manual configuration

For additional workspaces or manual setup, copy the examples:

```bash
cp .env.example .env
cp config.example.toml config.toml
```

Each `[channels."ID"]` entry in `config.toml` maps one Discord channel to one workspace. Only listed
users and mapped channels are processed.

Run the manually configured instance with:

```bash
pam-discord --env-file .env --config config.toml
```

For continuous availability, run Pam as a background service on a machine that stays online.

## Conversation record

```text
archive/conversations/<discord-thread-id>/
├── state.json
├── conversation.jsonl
└── messages/<discord-message-id>/
    ├── recording.ogg
    ├── metadata.json
    ├── transcript.txt
    ├── prompt.txt
    ├── codex-events.jsonl
    └── codex-output.txt
```

`conversation.jsonl` is the readable chronological record. It connects every author, message, transcript, prompt, reply, Discord link, project, and Codex session. Changes made by Codex remain in that project's workspace and should follow the project's branch and review rules.

## Current voice support

Discord can send native voice messages from its mobile apps. On desktop, record audio with your operating system and upload the file. Pam does not record live Discord calls. A direct desktop recording shortcut is planned.

## Security and privacy

- Never commit `.env`, `config.toml`, recordings, or private transcripts.
- Allowlist users and expose the bot only to necessary channels.
- Use private channels for private work.
- Obtain consent before recording other people.
- Define retention rules for audio and transcripts.

MIT licensed. Contributions are welcome; never include real recordings, credentials, or private paths in issues.
