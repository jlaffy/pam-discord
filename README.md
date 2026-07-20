# Pam Discord

Pam Discord lets you direct Codex work from Discord using voice recordings.

Send a voice message from your phone. Pam saves the original recording, transcribes it locally, runs the transcript as a Codex task inside the project mapped to that Discord channel, saves the result, and replies in Discord.

```text
voice instruction
  -> saved recording and transcript
  -> Codex acts in the selected project
  -> saved result and Discord reply
```

This creates a documented, trackable channel for prompting agents and reviewing what they did. Discord is the interface; the actual work happens in local project repositories.

Pam uses local `faster-whisper` transcription and a ChatGPT-authenticated Codex CLI. It does not require an OpenAI or Anthropic API key and never falls back to metered model APIs. Codex plan limits still apply.

> This is an early release. Test it in a non-critical project before relying on it.

## How it works

Pam is a small program that stays running on your computer or server. It connects to Discord using a free bot account and waits for recordings from approved users.

When a recording arrives:

1. Pam identifies the project from the Discord channel.
2. It downloads the recording and transcribes it locally.
3. It starts a new Codex task in that project's local repository.
4. Codex reads the project, performs the requested work, and returns a result.
5. Pam saves the recording, transcript, metadata, prompt, and result, then replies in Discord.

There is no scheduled polling and no Codex window left open. Pam waits for Discord events and launches `codex exec` only when work arrives.

```text
phone or computer                 always-on computer/server
Discord voice message  ------->  Pam -> local project -> Codex
Discord reply          <-------        saved archive <- result
```

## What to expect today

- Each mapped Discord channel points to one local project directory.
- Each recording starts a separate Codex task; follow-up conversation memory is not implemented yet.
- Codex may read and change files or run commands according to its permissions and the project's instructions.
- Pam does not automatically create branches, push commits, open PRs, or merge work.
- Replies appear after transcription and the Codex task finish; long tasks can take time.
- Pam must be running and online to receive new work.
- Native Discord voice recording is mobile-only; desktop users upload an audio file.

## Setup

You need Python 3.11+, a Discord server, an online computer or Linux server, and the [Codex CLI](https://developers.openai.com/codex/cli/) signed in with ChatGPT. Clone every project you want Pam to use onto that same machine.

### 1. Install

```bash
git clone https://github.com/jlaffy/pam-discord.git
cd pam-discord
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
codex login
```

On Windows, activate with `.venv\Scripts\Activate.ps1`.

### 2. Create a Discord bot

In the [Discord Developer Portal](https://discord.com/developers/applications):

1. Create an application and add a bot.
2. Enable **Message Content Intent**.
3. Invite it to your server with **View Channels**, **Read Message History**, **Send Messages**, and **Attach Files** permissions.
4. Copy its token. Keep the token private.

### 3. Configure

```bash
cp .env.example .env
cp config.example.toml config.toml
```

Add the Discord token to `.env`:

```dotenv
DISCORD_BOT_TOKEN=your-private-bot-token
```

Enable Discord **Developer Mode**, then copy your user ID and channel IDs. Edit `config.toml`:

```toml
archive_dir = "./archive"
allowed_user_ids = [111111111111111111]

[channels."222222222222222222"]
workspace = "/absolute/path/to/agent-native-genomics"
run_codex = true

[channels."333333333333333333"]
workspace = "/absolute/path/to/personal-workspace"
run_codex = true
```

Each channel points to one workspace—the local project directory where Codex will act. Only listed users and mapped channels are processed.

### 4. Run

```bash
pam-discord --config config.toml
```

Send a short voice message in a mapped channel. Pam should reply first with the transcript and then with the Codex result.

For continuous availability, run Pam as a background service on a machine that stays online.

Your first test should request something harmless, such as: “Read this project's README and summarize it without changing any files.” Confirm the transcript, reply, and archive before allowing editing tasks.

## Record of each instruction

```text
archive/YYYY/MM/DD/<discord-message-id>/
├── recording.ogg
├── metadata.json
├── transcript.txt
├── prompt.txt
└── codex-output.txt
```

The archive records what was requested, who requested it, which project received it, and what Codex returned. Changes made by Codex remain in that project's workspace and should follow the project's branch and review rules.

## Current voice support

Discord can send native voice messages from its mobile apps. On desktop, record audio with your operating system and upload the file. Pam does not record live Discord calls. A direct desktop recording shortcut is planned.

## Security and privacy

- Never commit `.env`, `config.toml`, recordings, or private transcripts.
- Allowlist users and expose the bot only to necessary channels.
- Use private channels for private work.
- Obtain consent before recording other people.
- Define retention rules for audio and transcripts.

MIT licensed. Contributions are welcome; never include real recordings, credentials, or private paths in issues.
