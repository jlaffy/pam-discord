# Pam

Pam gives you an always-on connection to Codex on a remote machine through Discord—using text or
voice from your phone or computer. Each Discord thread keeps its own Codex session, and Pam saves
the conversation.

## Set up in a few minutes

You need Python 3.11+, a Discord server, and the
[Codex CLI](https://developers.openai.com/codex/cli/).

### 1. Install

```bash
git clone https://github.com/jlaffy/pam-discord.git
cd pam-discord
./install.sh
codex login
```

### 2. Create a Discord bot

Open the [Discord Developer Portal](https://discord.com/developers/applications):

1. Create an application and add a bot.
2. Enable **Message Content Intent**.
3. Copy the bot token.

In Discord, enable **User Settings → Advanced → Developer Mode**, right-click your profile, and
copy your user ID.

### 3. Let Pam finish

```bash
./pam setup /path/to/project
```

Pam asks for your Discord user ID and the hidden bot token, then prints an installation link. Click
it and choose an existing Discord server. Pam creates a project area with `#main`, connects it to
the project directory, checks Codex, and starts its background service.

Pam then prints the direct Discord link. Send a text or voice message there and you are ready.

The project directory is the Pam workspace. Later, its Discord area can have more channels mapped
to project subdirectories. Setup needs SSH once; afterward Pam uses outbound encrypted connections
to Discord, needs no public inbound port, and keeps running when you disconnect.

## Useful commands

```bash
./pam doctor            # check the setup
./pam service status    # is Pam running?
./pam service logs      # recent activity and errors
./pam service restart   # restart Pam
./pam service stop      # stop Pam
./pam service start     # start Pam
```

Pam only accepts configured users and channels and only works in configured project directories.
Private tokens, recordings, session state, and archives stay outside the project repository.

MIT licensed.
