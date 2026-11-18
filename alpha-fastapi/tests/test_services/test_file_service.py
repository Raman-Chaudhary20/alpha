"""Unit tests for FileService."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.services.file_service import FileService


@pytest.fixture
def fs(tmp_path) -> FileService:
    svc = FileService(data_dir=str(tmp_path / "data"))
    svc.ensure_data_dir()
    return svc


class TestStamp:
    def test_stamp_format(self, fs):
        result = fs.stamp()
        # Should be "YYYY-MM-DD HH:MM" = 16 chars
        assert len(result) == 16
        assert result[4] == "-"
        assert result[7] == "-"
        assert result[10] == " "
        assert result[13] == ":"

    def test_stamp_with_explicit_dt(self, fs):
        dt = datetime(2024, 6, 15, 9, 5, tzinfo=timezone.utc)
        assert fs.stamp(dt) == "2024-06-15 09:05"


class TestEnsureDataDir:
    def test_creates_directory(self, tmp_path):
        target = tmp_path / "new_data"
        svc = FileService(data_dir=str(target))
        assert not target.exists()
        svc.ensure_data_dir()
        assert target.exists()

    def test_idempotent(self, fs):
        fs.ensure_data_dir()  # Should not raise


class TestTail:
    def test_tail_on_missing_file(self, fs):
        result = fs.tail(fs.data_dir / "missing.txt", 10)
        assert result == ""

    def test_tail_returns_tail_cut_at_line_boundary(self, tmp_path, fs):
        f = fs.data_dir / "sample.txt"
        lines = [f"line {i}" for i in range(1, 11)]
        f.write_text("\n".join(lines), encoding="utf-8")
        result = fs.tail(f, 25)
        assert "line 10" in result
        assert "line 9" in result

    def test_tail_n_larger_than_file(self, tmp_path, fs):
        f = fs.data_dir / "small.txt"
        f.write_text("a\nb\nc", encoding="utf-8")
        result = fs.tail(f, 100)
        assert result == "a\nb\nc"

    def test_tail_empty_file(self, fs):
        f = fs.data_dir / "empty.txt"
        f.write_text("", encoding="utf-8")
        result = fs.tail(f, 5)
        assert result == ""


class TestHead:
    def test_head_on_missing_file(self, fs):
        result = fs.head(fs.data_dir / "missing.txt", 5)
        assert result == ""

    def test_head_returns_first_n_chars(self, fs):
        f = fs.data_dir / "sample.txt"
        lines = [f"line {i}" for i in range(1, 11)]
        f.write_text("\n".join(lines), encoding="utf-8")
        result = fs.head(f, 20)
        assert "line 1" in result

    def test_head_n_larger_than_file(self, fs):
        f = fs.data_dir / "small.txt"
        f.write_text("x\ny", encoding="utf-8")
        result = fs.head(f, 100)
        assert result == "x\ny"


class TestSaveConversation:
    def test_creates_file_if_missing(self, fs):
        assert not fs.conversation_file.exists()
        fs.save_conversation("api", "alice", "Hello?", "Hi!")
        assert fs.conversation_file.exists()

    def test_content_includes_question_and_answer(self, fs):
        fs.save_conversation("api", "bob", "What time is it?", "It is noon.")
        content = fs.conversation_file.read_text(encoding="utf-8")
        assert "What time is it?" in content
        assert "It is noon." in content

    def test_multiple_saves_append(self, fs):
        fs.save_conversation("api", "alice", "Q1", "A1")
        fs.save_conversation("slack", "bob", "Q2", "A2")
        content = fs.conversation_file.read_text(encoding="utf-8")
        assert "Q1" in content
        assert "Q2" in content
        assert "A1" in content
        assert "A2" in content

    def test_explicit_timestamp(self, fs):
        dt = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
        fs.save_conversation("api", "tester", "question", "answer", dt=dt)
        content = fs.conversation_file.read_text(encoding="utf-8")
        assert "2024-01-01 12:00" in content


class TestAppendSlackLine:
    def test_creates_slack_file(self, fs):
        dt = datetime(2024, 3, 10, 8, 30, tzinfo=timezone.utc)
        fs.append_slack_line(dt, "general", "alice", "Hello everyone")
        assert fs.slack_file.exists()

    def test_line_format(self, fs):
        dt = datetime(2024, 3, 10, 8, 30, tzinfo=timezone.utc)
        fs.append_slack_line(dt, "general", "alice", "Hello everyone")
        content = fs.slack_file.read_text(encoding="utf-8")
        assert "[2024-03-10 08:30]" in content
        assert "#general" in content
        assert "alice" in content
        assert "Hello everyone" in content


class TestState:
    def test_load_state_missing_returns_empty(self, fs):
        assert fs.load_state() == {}

    def test_save_and_load_roundtrip(self, fs):
        state = {"C123": "1700000000.000000", "C456": "1700001000.000000"}
        fs.save_state(state)
        loaded = fs.load_state()
        assert loaded == state

    def test_load_state_corrupt_returns_empty(self, fs):
        fs.state_file.write_text("not json!!!", encoding="utf-8")
        assert fs.load_state() == {}
