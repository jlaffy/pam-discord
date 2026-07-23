from __future__ import annotations

import json
import subprocess
import asyncio
from pathlib import Path
from unittest.mock import patch

from pam_discord.bot import (
    PamDiscord,
    _clean_thread_title,
    _deliverable_paths,
    _disable_session_polling,
    _enable_session_polling,
    _load_polled_sessions,
    _recently_mirrored,
)
from pam_discord.app_server import save_shared_sessions
from pam_discord.config import ChannelConfig, Config


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


def test_new_task_is_saved_and_followup_resumes_same_session(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    conversation = tmp_path / "archive" / "conversations" / "123"
    first = conversation / "messages" / "1"
    second = conversation / "messages" / "2"
    workspace.mkdir()
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    session_id = "019f810a-a580-7200-9df7-40b523d9a878"

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        output_path = Path(command[command.index("-o") + 1])
        output_path.write_text("FIRST" if "resume" not in command else "SECOND", encoding="utf-8")
        event = json.dumps({"type": "thread.started", "thread_id": session_id}) + "\n"
        return subprocess.CompletedProcess(command, 0, stdout=event, stderr="")

    bot = _bot(tmp_path)
    with patch("pam_discord.bot.subprocess.run", side_effect=fake_run) as run:
        output1, returned1 = bot._run_codex("first", workspace, conversation, first)
        output2, returned2 = bot._run_codex("second", workspace, conversation, second)

    assert (output1, output2) == ("FIRST", "SECOND")
    assert returned1 == returned2 == session_id
    assert "resume" not in run.call_args_list[0].args[0]
    assert "resume" in run.call_args_list[1].args[0]
    assert "--dangerously-bypass-approvals-and-sandbox" in run.call_args_list[0].args[0]
    assert "--dangerously-bypass-approvals-and-sandbox" in run.call_args_list[1].args[0]
    assert json.loads((conversation / "state.json").read_text())["codex_session_id"] == session_id
    assert (first / "codex-events.jsonl").exists()
    assert (second / "codex-events.jsonl").exists()


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


def test_live_events_disable_compatibility_polling(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    _enable_session_polling(workspace, "thread-1")

    _disable_session_polling(workspace, "thread-1")

    assert _load_polled_sessions(workspace) == set()


def test_recent_mirror_content_is_deduplicated_across_different_item_ids() -> None:
    cache: dict[tuple[str, str, str], float] = {}
    key = ("thread-1", "agentMessage", "same response")

    assert _recently_mirrored(cache, key, 10) is False
    assert _recently_mirrored(cache, key, 12) is True
    assert _recently_mirrored(cache, key, 30) is False


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
