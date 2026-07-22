# Pam

Talk to Codex from Discord using text or voice. Pam works inside a project on your server, keeps
each Discord thread connected to one Codex session, and saves the conversation.

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

### 2. Create the Discord bot

Open the [Discord Developer Portal](https://discord.com/developers/applications):

1. Create an application and add a bot.
2. Enable **Message Content Intent**.
3. Invite it to your server with **View Channels**, **Read Message History**, **Send Messages**,
   **Create Public Threads**, and **Send Messages in Threads**.
4. Copy the bot token.

In Discord, enable **User Settings → Advanced → Developer Mode**. Right-click your profile and the
project channel to copy their IDs.

### 3. Let Pam finish

```bash
./pam setup
./pam doctor
./pam service install
```

The setup guide asks for the token, IDs, and project directory. The token is entered privately and
is never shown. `doctor` checks Discord, Codex, and the project. The service command keeps Pam
running after you log out and restarts it after failures.

Now send a text or voice message in the project channel. Pam creates a thread, runs Codex, replies
there, and resumes the same session for follow-ups.

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
