from __future__ import annotations

import json
import asyncio
from pathlib import Path
from types import SimpleNamespace

from pam_discord.bot import (
    ConnectorApprovalView,
    ConnectorOAuthView,
    DISCORD_AGENT_INSTRUCTION,
    PamDiscord,
    _allowed_project_roots,
    _clean_thread_title,
    _deliverable_paths,
    _disable_session_polling,
    _enable_session_polling,
    _load_polled_sessions,
    _recently_mirrored,
    _remote_project_command,
)
from pam_discord.app_server import load_shared_sessions, save_shared_sessions
from pam_discord.config import ChannelConfig, Config, load_config


def _bot(tmp_path: Path) -> PamDiscord:
    return PamDiscord(
        Config(
            archive_dir=tmp_path / "archive",
            allowed_user_ids=frozenset({1}),
            channels={},
            guilds={},
            max_attachment_bytes=1024,
            max_audio_seconds=60,
            whisper_model="tiny.en",
            whisper_device="cpu",
            whisper_compute_type="int8",
            codex_binary="codex",
            codex_timeout_seconds=60,
            codex_app_server_url="ws://127.0.0.1:45832",
        )
    )


def test_discord_session_is_mapped_before_app_server_turn_starts(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "project"
    conversation = tmp_path / "archive" / "conversations" / "123"
    project_conversation = workspace / ".pam" / "conversations" / "123"
    workspace.mkdir()
    conversation.mkdir(parents=True)
    project_conversation.mkdir(parents=True)
    (project_conversation / "metadata.json").write_text(
        json.dumps({"discord_thread_id": 123}) + "\n", encoding="utf-8"
    )
    channel = ChannelConfig(workspace=workspace, project_record_dir=workspace / ".pam")
    bot = _bot(tmp_path)

    class DiscordThread:
        id = 123

    monkeypatch.setattr("pam_discord.bot.discord.Thread", DiscordThread)
    calls: list[tuple[str, dict[str, object]]] = []

    async def request(method: str, params: dict[str, object]) -> dict[str, object]:
        calls.append((method, params))
        if method == "thread/start":
            return {"thread": {"id": "codex-thread"}}
        assert load_shared_sessions(workspace) == {"codex-thread": 123}
        return {"turn": {"id": "turn-1", "status": "inProgress"}}

    bot._app_server.request = request  # type: ignore[method-assign]

    returned = asyncio.run(
        bot._start_discord_codex_session(
            channel,
            DiscordThread(),
            456,
            "Do the work",
            conversation,
            project_conversation,
        )
    )

    assert returned == "codex-thread"
    assert [method for method, _params in calls] == ["thread/start", "turn/start"]
    assert calls[0][1]["cwd"] == str(workspace)
    assert calls[1][1]["threadId"] == "codex-thread"
    assert calls[1][1]["clientUserMessageId"] == "discord:456"
    assert json.loads((conversation / "state.json").read_text())[
        "codex_session_id"
    ] == "codex-thread"
    assert json.loads((project_conversation / "metadata.json").read_text())[
        "codex_thread_id"
    ] == "codex-thread"


def test_always_on_config_discovers_git_project_without_channel_mappings(
    tmp_path: Path,
) -> None:
    root = tmp_path / "projects"
    project = root / "sp_atlas"
    cwd = project / "results" / "mutagenesis"
    cwd.mkdir(parents=True)
    (project / ".git").mkdir()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                f'archive_dir = "{tmp_path / "archive"}"',
                "allowed_user_ids = [1]",
                "",
                "[always_on]",
                "guild_id = 99",
                f'approved_roots = ["{root}"]',
                f'state_dir = "{tmp_path / "canary"}"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    config = load_config(config_path)
    bot = PamDiscord(config)
    discovered = bot._workspace_config_for_cwd(cwd)

    assert config.always_on_guild_id == 99
    assert discovered is not None
    assert discovered.workspace == project
    assert discovered.project_record_dir is not None
    assert discovered.project_record_dir.is_relative_to(tmp_path / "canary")
    assert discovered.session_state_dir != project


def test_always_on_config_excludes_production_owned_projects(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    excluded = root / "production-project"
    candidate = root / "lab-project"
    excluded.mkdir(parents=True)
    candidate.mkdir()
    (excluded / ".git").mkdir()
    (candidate / ".git").mkdir()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                f'archive_dir = "{tmp_path / "archive"}"',
                "allowed_user_ids = [1]",
                "",
                "[always_on]",
                "guild_id = 99",
                f'approved_roots = ["{root}"]',
                f'excluded_roots = ["{excluded}"]',
                f'state_dir = "{tmp_path / "canary"}"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    bot = PamDiscord(load_config(config_path))

    assert bot._workspace_config_for_cwd(excluded) is None
    assert bot._workspace_config_for_cwd(excluded / "results") is None
    assert bot._workspace_config_for_cwd(candidate) is not None


def test_always_on_directory_index_groups_sessions_by_relative_cwd(
    tmp_path: Path,
) -> None:
    project = tmp_path / "sp_atlas"
    records = tmp_path / "state" / "conversations"
    project.mkdir()
    for thread_id, relative, title in (
        (101, ".", "Plan analysis"),
        (102, "results/mutagenesis", "Review VEGFA peaks"),
    ):
        conversation = records / str(thread_id)
        conversation.mkdir(parents=True)
        (conversation / "metadata.json").write_text(
            json.dumps(
                {
                    "discord_thread_id": thread_id,
                    "workspace": str((project / relative).resolve()),
                    "title": title,
                }
            )
            + "\n",
            encoding="utf-8",
        )
    bot = _bot(tmp_path)
    object.__setattr__(bot.config, "always_on_guild_id", 99)
    channel = ChannelConfig(
        workspace=project,
        project_root=project,
        project_record_dir=records,
        session_state_dir=tmp_path / "state",
    )

    text = bot._directory_index_text(channel)

    assert "sp_atlas/" in text
    assert "root/" in text
    assert "results/" in text
    assert "mutagenesis/" in text
    assert "Plan analysis" in text
    assert "Review VEGFA peaks" in text
    assert "https://discord.com/channels/99/102" in text


def test_always_on_catalog_checkpoint_skips_unchanged_sessions(
    tmp_path: Path,
) -> None:
    root = tmp_path / "projects"
    project = root / "project"
    project.mkdir(parents=True)
    bot = _bot(tmp_path)
    object.__setattr__(bot.config, "always_on_guild_id", 99)
    object.__setattr__(bot.config, "always_on_state_dir", tmp_path / "state")
    object.__setattr__(bot.config, "project_roots", (root,))
    value = {
        "id": "codex-thread",
        "cwd": str(project),
        "preview": "First prompt",
        "updatedAt": 1,
    }
    linked: list[str] = []

    async def request(_method: str, _params: dict[str, object]) -> dict[str, object]:
        return {"data": [dict(value)], "nextCursor": None}

    async def link(thread: dict[str, object]) -> None:
        linked.append(str(thread["id"]))

    bot._app_server.request = request  # type: ignore[method-assign]
    bot._link_started_codex_thread = link  # type: ignore[method-assign]

    async def exercise() -> None:
        await bot._sync_project_session_catalogs()
        await bot._sync_project_session_catalogs()
        value["updatedAt"] = 2
        await bot._sync_project_session_catalogs()

    asyncio.run(exercise())

    assert linked == ["codex-thread", "codex-thread"]
    checkpoint = json.loads(
        (tmp_path / "state" / "catalog-checkpoint.json").read_text()
    )
    assert checkpoint["initialized"] is True


def test_linked_terminal_sessions_are_polled_for_new_turns(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    channel = ChannelConfig(workspace=workspace, project_record_dir=tmp_path / "records")
    bot = _bot(tmp_path)
    bot.config.guilds[10] = channel
    save_shared_sessions(workspace, {"codex-thread": 123})
    _enable_session_polling(workspace, "codex-thread")
    imported: list[str] = []

    async def import_history(thread_id: str) -> None:
        imported.append(thread_id)

    bot._import_codex_history = import_history  # type: ignore[method-assign]
    asyncio.run(bot._sync_shared_sessions())

    assert imported == ["codex-thread"]


def test_normal_shared_sessions_are_not_polled(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    channel = ChannelConfig(workspace=workspace, project_record_dir=tmp_path / "records")
    bot = _bot(tmp_path)
    bot.config.guilds[10] = channel
    save_shared_sessions(workspace, {"live-thread": 123})
    imported: list[str] = []

    async def import_history(thread_id: str) -> None:
        imported.append(thread_id)

    bot._import_codex_history = import_history  # type: ignore[method-assign]
    asyncio.run(bot._sync_shared_sessions())

    assert imported == []


def test_discord_started_session_is_relinked_without_duplicate_thread(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "project"
    records = workspace / ".pam" / "conversations"
    original_record = records / "123"
    workspace.mkdir()
    original_record.mkdir(parents=True)
    (original_record / "metadata.json").write_text(
        json.dumps({"discord_thread_id": 123}) + "\n", encoding="utf-8"
    )
    channel = ChannelConfig(workspace=workspace, project_record_dir=records)
    bot = _bot(tmp_path)
    bot.config.guilds[10] = channel

    class OriginalThread:
        id = 123

    original = OriginalThread()
    monkeypatch.setattr("pam_discord.bot.discord.Thread", OriginalThread)

    async def request(method: str, params: dict[str, object]) -> dict[str, object]:
        assert method == "thread/read"
        assert params == {"threadId": "codex-thread", "includeTurns": True}
        return {
            "thread": {
                "turns": [
                    {
                        "items": [
                            {"type": "userMessage", "clientId": "discord:123"}
                        ]
                    }
                ]
            }
        }

    bot._app_server.request = request  # type: ignore[method-assign]
    bot.get_channel = lambda channel_id: original if channel_id == 123 else None  # type: ignore[method-assign]
    authorized: list[int] = []

    async def authorize(thread_id: int) -> None:
        authorized.append(thread_id)

    bot._ensure_authorized_thread_members = authorize  # type: ignore[method-assign]

    asyncio.run(
        bot._link_started_codex_thread_once(
            {
                "id": "codex-thread",
                "cwd": str(workspace),
                "name": "Refined Pam conversations",
            }
        )
    )

    assert json.loads((workspace / ".pam" / "shared-sessions.json").read_text()) == {
        "codex-thread": 123
    }
    assert json.loads((original_record / "metadata.json").read_text())[
        "codex_thread_id"
    ] == "codex-thread"
    assert authorized == [123]


def test_live_events_disable_compatibility_polling(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    _enable_session_polling(workspace, "thread-1")

    _disable_session_polling(workspace, "thread-1")

    assert _load_polled_sessions(workspace) == set()


def test_typing_spans_the_complete_app_server_turn(tmp_path: Path, monkeypatch) -> None:
    bot = _bot(tmp_path)
    lifecycle: list[str] = []

    class Typing:
        async def __aenter__(self):
            lifecycle.append("start")

        async def __aexit__(self, *_args):
            lifecycle.append("stop")

    class DiscordThread:
        id = 123

        def typing(self):
            return Typing()

    monkeypatch.setattr("pam_discord.bot.discord.Thread", DiscordThread)
    bot._discord_thread_for_codex = lambda _thread_id: 123  # type: ignore[method-assign]
    bot.get_channel = lambda _channel_id: DiscordThread()  # type: ignore[method-assign]

    async def exercise() -> None:
        await bot._handle_app_server_notification(
            {"method": "turn/started", "params": {"threadId": "codex-thread"}}
        )
        assert lifecycle == ["start"]
        await bot._handle_app_server_notification(
            {"method": "turn/completed", "params": {"threadId": "codex-thread"}}
        )

    asyncio.run(exercise())
    assert lifecycle == ["start", "stop"]


def test_recent_mirror_content_is_deduplicated_across_different_item_ids() -> None:
    cache: dict[tuple[str, str, str], float] = {}
    key = ("thread-1", "agentMessage", "same response")

    assert _recently_mirrored(cache, key, 10) is False
    assert _recently_mirrored(cache, key, 12) is True
    assert _recently_mirrored(cache, key, 30) is False


def test_discord_instruction_prefers_existing_tools_without_connector_prompts() -> None:
    assert "existing authenticated local command-line tools" in DISCORD_AGENT_INSTRUCTION
    assert "only when" in DISCORD_AGENT_INSTRUCTION


def test_connector_approval_defaults_to_decline() -> None:
    view = ConnectorApprovalView(frozenset({1}))
    assert view.action == "decline"
    assert view.allowed_user_ids == frozenset({1})


def test_connector_oauth_view_keeps_authorization_url_out_of_message_text() -> None:
    view = ConnectorOAuthView(frozenset({1}), "https://example.com/oauth")
    links = [
        item
        for item in view.children
        if isinstance(item, __import__("discord").ui.Button) and item.url
    ]
    assert [item.url for item in links] == ["https://example.com/oauth"]
    assert view.action == "decline"


def test_connector_suggestion_is_declined_instead_of_hanging(tmp_path: Path) -> None:
    bot = _bot(tmp_path)
    responses: list[tuple[int, dict[str, object]]] = []

    async def respond(request_id: int, result: dict[str, object]) -> None:
        responses.append((request_id, result))

    bot._app_server.respond = respond  # type: ignore[method-assign]
    event: dict[str, object] = {
        "method": "mcpServer/elicitation/request",
        "id": 7,
        "params": {
            "message": "Connect GitHub",
            "_meta": {"codex_approval_kind": "tool_suggestion"},
        },
    }

    asyncio.run(bot._handle_app_server_notification(event))

    assert responses == [(7, {"action": "decline"})]


def test_deliverables_are_limited_to_supported_project_files(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    plot = workspace / "results" / "plot.png"
    plot.parent.mkdir()
    plot.write_bytes(b"png")
    secret = tmp_path / "secret.pdf"
    secret.write_bytes(b"secret")
    source = workspace / "analysis.py"
    source.write_text("pass\n")

    text = f"See [plot]({plot}) and `{secret}` and `{source}` and [plot again]({plot}:12)."

    assert _deliverable_paths(text, workspace) == [plot]


def test_generated_thread_titles_are_cleaned_and_limited() -> None:
    assert _clean_thread_title('  **Title: Better Discord voice threads**\n') == (
        "Better Discord voice threads"
    )
    assert len(_clean_thread_title("word " * 30)) <= 80


def test_remote_project_command_parses_add_and_create(tmp_path: Path) -> None:
    project = tmp_path / "project with spaces"

    assert _remote_project_command(f'pam project add "{project}"') == (
        "connect",
        project,
    )
    assert _remote_project_command(f'pam project connect "{project}"') == (
        "connect",
        project,
    )
    assert _remote_project_command(f'pam project create "{project}"') == (
        "create",
        project,
    )
    assert _remote_project_command("please add a project") is None


def test_remote_project_roots_are_limited_to_configured_project_parents(
    tmp_path: Path,
) -> None:
    first = tmp_path / "owner" / "first"
    second = tmp_path / "other-owner" / "second"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    bot = _bot(tmp_path)
    bot.config.guilds[1] = ChannelConfig(workspace=first)
    bot.config.guilds[2] = ChannelConfig(workspace=second)

    assert _allowed_project_roots(bot.config) == {first.parent, second.parent}


def test_remote_project_configuration_is_persisted_and_available_immediately(
    tmp_path: Path,
) -> None:
    existing = tmp_path / "existing"
    workspace = tmp_path / "pam-vignettes"
    state_dir = tmp_path / "state"
    existing.mkdir()
    workspace.mkdir()
    state_dir.mkdir()
    config_path = state_dir / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                f'archive_dir = "{state_dir / "archive"}"',
                "allowed_user_ids = [1]",
                "",
                '[channels."20"]',
                f'workspace = "{existing}"',
                "run_codex = true",
                "",
                '[guilds."10"]',
                f'workspace = "{existing}"',
                "run_codex = true",
                "",
            ]
        ),
        encoding="utf-8",
    )
    bot = _bot(tmp_path)
    object.__setattr__(bot.config, "config_path", config_path)
    bot.config.guilds[10] = ChannelConfig(workspace=existing, project_root=existing)
    sent: list[str] = []
    replies: list[str] = []

    class Member:
        async def edit(self, *, nick: str) -> None:
            assert nick == "pam"

    class Channel:
        id = 40
        name = "general"
        jump_url = "https://discord.com/channels/30/40"

        async def send(self, text: str) -> None:
            sent.append(text)

    class Message:
        async def reply(self, text: str, *, mention_author: bool) -> None:
            assert mention_author is False
            replies.append(text)

    channel = Channel()
    guild = SimpleNamespace(id=30, me=Member(), text_channels=[channel])

    asyncio.run(
        bot._configure_remote_project(
            Message(),  # type: ignore[arg-type]
            workspace,
            guild,  # type: ignore[arg-type]
        )
    )

    persisted = load_config(config_path)
    assert persisted.guilds[30].workspace == workspace
    assert persisted.channels[40].workspace == workspace
    assert bot.config.guilds[30].workspace == workspace
    assert bot.config.channels[40].workspace == workspace
    assert (workspace / ".gitignore").read_text(encoding="utf-8") == ".pam/\n"
    assert sent == [
        f"**pam** · `{workspace}` is connected. Send a message here to start a conversation."
    ]
    assert replies == ["Project connected: https://discord.com/channels/30/40"]
