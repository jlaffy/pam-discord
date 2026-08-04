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
  sorted by Codex `updatedAt` and shows status, project root, relative working directory, token
  total, and a link to each existing Forum post. It never creates duplicate conversations.

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

Pam must not expose private hidden model reasoning. It may mirror reasoning summaries and tool
events that Codex explicitly provides through its supported interfaces.
