# How pam works

**pam is a small bridge program that runs continuously on your remote computer. It connects
Discord messages to Codex sessions running against your project directories.**

pam is software, not separate hardware. The hardware is the remote computer where pam, Codex, and
your projects run.

## The main pieces

```text
Your phone or computer
        ↓
Discord's internet service
        ↓
Discord bot account
        ↓ persistent connection using a private token
pam process on your remote computer
        ↓
Codex on your remote computer
        ↓
the selected project directory
```

## Where each part lives

### 1. Discord

Discord's servers, messages, threads, and bot account live in Discord's cloud, not on your remote
computer.

You create the pam application and bot account in the
[Discord Developer Portal](https://discord.com/developers/applications). Discord gives the bot a
private token. pam stores a copy on the remote computer in:

```text
~/.local/share/pam-discord/.env
```

The token lets pam sign in as the Discord bot. Treat it like a password: never commit or share it.

### 2. The pam software

The pam repository can live anywhere on the remote computer. For example:

```text
/path/to/pam-discord
```

The installer creates a Python environment inside the repository and starts one pam process in the
background. That process stays available after you disconnect from the remote computer.

This one running process is the central pam bridge. pam does not install a separate copy of itself
inside every project.

### 3. pam's central configuration

pam keeps its private configuration and operational data outside the repository:

```text
~/.local/share/pam-discord/
```

Important files include:

```text
.env                    private Discord bot token
config.toml             Discord-to-project mappings and runtime settings
identity.json           authorized Discord user identity
pam.log                 activity and error log
archive/                central conversation records
background-service.json information about the running background process
```

### 4. Codex

Codex runs on the same remote computer. pam talks to a local Codex app-server, which by default
listens at:

```text
ws://127.0.0.1:45832
```

That address is local to the remote computer. Discord does not connect directly to Codex:
Discord talks to pam, and pam talks to Codex.

### 5. Individual projects

Adding a project does not install another pam. It adds a mapping to the central configuration:

```text
Discord server: my-project
        ↕
Project directory: /path/to/my-project
```

Each connected project gets a small `.pam/` directory for its conversation records and
Discord-to-Codex session mappings:

```text
/path/to/my-project/.pam/
```

During setup, you choose whether Git ignores this directory or allows its conversation history to
be committed.

## Central pam versus project-specific pam data

- **Central pam:** one running bridge, one Discord bot token, and one configuration covering all
  connected projects.
- **Project-specific pam data:** a lightweight `.pam/` directory plus a central mapping between
  that project directory and its Discord server.
- **Discord project server:** the remote user interface for that project, hosted by Discord.

In one sentence:

> pam is a self-hosted bridge that runs on the same remote computer as your projects and Codex,
> turning each Discord server into a remote interface for one project directory.
