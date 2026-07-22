from __future__ import annotations

import json
import subprocess
import asyncio
from pathlib import Path
from unittest.mock import patch

from pam_discord.bot import PamDiscord
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
    imported: list[str] = []

    async def import_history(thread_id: str) -> None:
        imported.append(thread_id)

    bot._import_codex_history = import_history  # type: ignore[method-assign]
    asyncio.run(bot._sync_shared_sessions())

    assert imported == ["codex-thread"]
