# Always-on Pam experiment

## Goal

Pam automatically discovers every Codex session under configured approved roots and presents it as
the same live conversation in the terminal and a central Discord server. No per-project Pam setup
is required.

## MVP parity requirements

- Preserve complete prompts, replies, transcripts, Codex events, execution traces, timestamps,
  authors, session IDs, and origins.
- Keep Discord and terminal views synchronized with minimal added latency.
- Show Discord typing for the full active Codex turn.
- Preserve current voice transcription and Codex-to-Discord file delivery.
- Resume one shared Codex session from either interface.
- Recover after Pam or Codex restarts without missing or duplicating messages.
- Keep Codex session data canonical; store only Pam mappings, checkpoints, records, audio, and
  attachments needed for recovery and portable history.

## Experimental organization

```text
Discord server: Pam Lab
  Text channel: #recent-sessions cross-project Codex recency dashboard
  Forum channel: one per discovered project
    Forum post/thread: one per Codex session
    Pinned directory index: virtual tree of directories that contain sessions
```

- Git roots define projects when available.
- Otherwise, the first directory beneath an approved root defines the project.
- A session's relative working directory is shown in its post title and project directory index.
- Codex's native session name is canonical. Pam mirrors `thread/name/updated` onto the already
  mapped Discord post and only adds directory decoration; it never generates a competing title
  or writes Discord title changes back into Codex.
- Directory indexes are event-driven, debounced, and periodically reconciled from lightweight
  metadata. They never block conversation mirroring.
- Forums default to latest-activity ordering. Each directory-index section also sorts by latest
  activity and shows running/idle state, relative creation and activity times, and best-effort
  token totals. `always_on.index_sessions_per_directory` controls how many recent sessions each
  section displays; older Forum posts remain searchable and are not deleted.
- `#recent-sessions` is a pinned, Pam-maintained dashboard placed at the top of the server. It is
  sorted by Codex `updatedAt`. Each entry leads with project root and relative working directory,
  then shows session age, last activity, status, token total, and the Codex-native linked title.
  It never creates duplicate conversations.
- Pam keeps the authorized user joined to at most three sessions per Forum that were active in
  the last seven days. These are sidebar shortcuts only; opening the Forum still shows its full
  post history. Trimming older memberships requires the bot's `Manage Threads` permission.

## Safety and rollout

- Develop on `experiment/always-on-pam` in an isolated worktree.
- Run with a separate `pam-lab` Discord bot, `Pam Lab` server, config, state directory, and test
  project.
- Do not modify or stop the live `main` Pam service during canary development.
- Keep existing Pam and Codex history unchanged and importable.

## Later iterations

- Accept Discord-uploaded screenshots, images, and text files as Codex inputs.
- Mirror richer live tool progress and available reasoning summaries.
- Add manual directory routing and promotion of busy subdirectories to dedicated Forum channels.
- Import existing project histories into the new index without duplicating Discord messages.

### Hybrid project destinations

Central Forums and dedicated project servers should be supported simultaneously rather than as
mutually exclusive operating modes. Route through a stable Pam project identity instead of
coupling a Codex session directly to a physical Discord destination:

```text
Codex session → Pam project identity → selected Discord destination
```

- New and smaller projects may default to a Forum in the central Pam server.
- Substantial, collaborative, or permission-sensitive projects may use dedicated Discord servers.
- A `pam promote PATH` operation should route a central project to a dedicated server, recreate
  desired session history from canonical Codex records, continue future syncing there, and leave
  the old Forum archived or read-only.
- A `pam consolidate PATH` operation should route a dedicated project back into the central
  server and optionally reconstruct its history there.
- Promotion and consolidation create new Discord threads from Codex history; they do not attempt
  to move Discord thread IDs between servers.
- Only one destination should actively write to a given Codex session at a time. Historical
  destinations may remain readable.
- Configuration should allow per-project destination overrides while preserving automatic
  discovery for projects without overrides.

Pam must not expose private hidden model reasoning. It may mirror reasoning summaries and tool
events that Codex explicitly provides through its supported interfaces.
