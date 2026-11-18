"""File I/O helpers for Alpha — a Python port of the server.js helpers."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class FileService:
    """Encapsulates all flat-file read/write operations used by Alpha."""

    def __init__(self, data_dir: str = "data") -> None:
        self._data_dir = Path(data_dir)

    # ------------------------------------------------------------------
    # Path properties
    # ------------------------------------------------------------------

    @property
    def data_dir(self) -> Path:
        return self._data_dir

    @property
    def conversation_file(self) -> Path:
        return self._data_dir / "conversation.md"

    @property
    def slack_file(self) -> Path:
        return self._data_dir / "slack_messages.txt"

    @property
    def state_file(self) -> Path:
        return self._data_dir / "slack_sync_state.json"

    @property
    def members_file(self) -> Path:
        return self._data_dir / "slack_members.txt"

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def stamp(dt: datetime | None = None) -> str:
        """Return a "YYYY-MM-DD HH:MM" timestamp (UTC by default)."""
        if dt is None:
            dt = datetime.now(tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M")

    def ensure_data_dir(self) -> None:
        """Create the data directory if it does not exist."""
        self._data_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------

    def tail(self, file_path: Path, n: int) -> str:
        """Last n chars of a file, cut at a line boundary so no line is half-sent."""
        if not file_path.exists():
            return ""
        data = file_path.read_text(encoding="utf-8")
        if len(data) <= n:
            return data
        cut = data[-n:]
        newline_idx = cut.find("\n")
        if newline_idx != -1:
            return cut[newline_idx + 1:]
        return cut

    def head(self, file_path: Path, n: int) -> str:
        """First n chars of a file, cut at a line boundary."""
        if not file_path.exists():
            return ""
        data = file_path.read_text(encoding="utf-8")
        if len(data) <= n:
            return data
        cut = data[:n]
        last_newline_idx = cut.rfind("\n")
        if last_newline_idx != -1:
            return cut[:last_newline_idx]
        return cut

    def read_tail_chars(self, file_path: Path, max_chars: int) -> str:
        """Alias for tail(file_path, max_chars)."""
        return self.tail(file_path, max_chars)

    def read_head_chars(self, file_path: Path, max_chars: int) -> str:
        """Alias for head(file_path, max_chars)."""
        return self.head(file_path, max_chars)

    # ------------------------------------------------------------------
    # Write helpers
    # ------------------------------------------------------------------

    def save_conversation(
        self,
        source: str,
        who: str,
        question: str,
        answer: str,
        dt: datetime | None = None,
    ) -> None:
        """Append a Q&A exchange to conversation.md."""
        ts = self.stamp(dt)
        q_clean = question.replace("\n", " ")
        a_clean = answer.replace("\n", " ")
        block = (
            f"### [{ts}] {source} - {who}\n"
            f"**Q:** {q_clean}\n"
            f"**A:** {a_clean}\n\n"
        )
        try:
            with self.conversation_file.open("a", encoding="utf-8") as fh:
                fh.write(block)
        except OSError:
            logger.exception("Failed to write conversation file")

    def append_slack_line(
        self,
        dt: datetime,
        channel: str,
        who: str,
        text: str,
    ) -> None:
        """Append a single Slack message line to slack_messages.txt."""
        ts = self.stamp(dt)
        clean_text = text.replace("\n", " ")
        line = f"[{ts}] (#{channel}) {who}: {clean_text}\n"
        try:
            with self.slack_file.open("a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError:
            logger.exception("Failed to write slack messages file")

    # ------------------------------------------------------------------
    # State (JSON)
    # ------------------------------------------------------------------

    def load_state(self) -> dict[str, Any]:
        """Load sync state from JSON; returns empty dict if missing/corrupt."""
        if not self.state_file.exists():
            return {}
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.warning("Could not load state file; starting fresh")
            return {}

    def save_state(self, state: dict[str, Any]) -> None:
        """Persist sync state to JSON."""
        try:
            self.state_file.write_text(
                json.dumps(state, indent=2, default=str),
                encoding="utf-8",
            )
        except OSError:
            logger.exception("Failed to save state file")
