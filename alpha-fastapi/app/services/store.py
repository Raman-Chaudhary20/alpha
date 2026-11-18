"""Alpha's memory store: every Slack message, file and summary, in one SQLite file.

Why SQLite and why these keys:
- A Slack message is identified by its conversation and its timestamp. A DM between two
  people is ONE conversation (one id) whichever person's sign-in reads it, so keying
  messages on (conversation_id, ts) stores each message once, however many times and
  through however many people it is read. Rereading only updates (edits, new replies).
- Each conversation has a kind (public, private, im, mpim). Only public channels are
  visible to people who are not admins; every query that returns content takes a scope
  ("all" or "public") and filters on it, so the rule lives here and not in a prompt.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCOPES = ("all", "public")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    real_name TEXT,
    title TEXT,
    is_admin INTEGER NOT NULL DEFAULT 0,
    is_bot INTEGER NOT NULL DEFAULT 0,
    deleted INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,              -- public | private | im | mpim
    name TEXT NOT NULL,
    members TEXT NOT NULL DEFAULT '[]',
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    conversation_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    thread_ts TEXT,
    user_id TEXT,
    text TEXT NOT NULL,
    reply_count INTEGER NOT NULL DEFAULT 0,
    files TEXT NOT NULL DEFAULT '[]',
    stored_at REAL NOT NULL,
    PRIMARY KEY (conversation_id, ts)
);
CREATE INDEX IF NOT EXISTS messages_user ON messages(user_id);
CREATE INDEX IF NOT EXISTS messages_stored ON messages(stored_at);
CREATE TABLE IF NOT EXISTS files (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    message_ts TEXT,
    user_id TEXT,
    name TEXT NOT NULL,
    mimetype TEXT,
    size INTEGER,
    text TEXT,
    stored_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS user_tokens (
    user_id TEXT PRIMARY KEY,
    token_enc TEXT NOT NULL,
    scopes TEXT NOT NULL,
    team_id TEXT,
    granted_at REAL NOT NULL,
    failed_at REAL
);
CREATE TABLE IF NOT EXISTS sync_state (
    conversation_id TEXT PRIMARY KEY,
    last_ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS digests (
    conversation_id TEXT NOT NULL,
    day TEXT NOT NULL,
    data TEXT NOT NULL,
    message_count INTEGER NOT NULL,
    built_at REAL NOT NULL,
    PRIMARY KEY (conversation_id, day)
);
CREATE TABLE IF NOT EXISTS knowledge (
    scope TEXT NOT NULL,
    kind TEXT NOT NULL,              -- project | person
    key TEXT NOT NULL,
    data TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (scope, kind, key)
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
    doc_id UNINDEXED, conversation_id UNINDEXED, ts UNINDEXED, text,
    tokenize = 'porter unicode61'
);
"""

_WORD = re.compile(r"[\w][\w'.-]*", re.UNICODE)
_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is", "are", "was", "were",
    "what", "who", "how", "many", "which", "with", "about", "did", "do", "does", "any", "all",
    "me", "tell", "show", "list", "there", "this", "that", "be", "it", "at", "by", "from",
}


def key_of(name: str) -> str:
    """A stable key for a project or person name: lower case, single spaces."""
    return re.sub(r"\s+", " ", name.strip().lower())


def day_of(ts: str) -> str:
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d")


def stamp_of(ts: str) -> str:
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def _norm(text: str) -> str:
    return re.sub(r"\W+", " ", text.lower()).strip()


class Store:
    """Thread-safe access to data/alpha.db."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(_SCHEMA)
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _all(self, sql: str, args: tuple | list = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    def _one(self, sql: str, args: tuple | list = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._db.execute(sql, args).fetchone()

    def _write(self, sql: str, args: tuple | list = ()) -> None:
        with self._lock:
            self._db.execute(sql, args)
            self._db.commit()

    # ------------------------------------------------------------------ meta
    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self._one("SELECT value FROM meta WHERE key = ?", (key,))
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        self._write(
            "INSERT INTO meta(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    # ------------------------------------------------------------------ users
    def upsert_user(self, user: dict[str, Any]) -> None:
        profile = user.get("profile") or {}
        name = (
            profile.get("display_name")
            or profile.get("real_name")
            or user.get("real_name")
            or user.get("name")
            or user["id"]
        )
        self._write(
            "INSERT INTO users(id, name, real_name, title, is_admin, is_bot, deleted, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
            "name = excluded.name, real_name = excluded.real_name, title = excluded.title, "
            "is_admin = excluded.is_admin, is_bot = excluded.is_bot, deleted = excluded.deleted, "
            "updated_at = excluded.updated_at",
            (
                user["id"],
                name,
                profile.get("real_name") or user.get("real_name") or "",
                profile.get("title") or "",
                1 if (user.get("is_admin") or user.get("is_owner") or user.get("is_primary_owner")) else 0,
                1 if (user.get("is_bot") or user["id"] == "USLACKBOT") else 0,
                1 if user.get("deleted") else 0,
                time.time(),
            ),
        )

    def user_name(self, user_id: str | None) -> str | None:
        if not user_id:
            return None
        row = self._one("SELECT name FROM users WHERE id = ?", (user_id,))
        return row["name"] if row else None

    def users(self, people_only: bool = True) -> list[dict]:
        sql = "SELECT * FROM users"
        if people_only:
            sql += " WHERE is_bot = 0 AND deleted = 0"
        return [dict(r) for r in self._all(sql + " ORDER BY name")]

    def is_workspace_admin(self, user_id: str) -> bool:
        row = self._one("SELECT is_admin FROM users WHERE id = ?", (user_id,))
        return bool(row and row["is_admin"])

    # ------------------------------------------------------------------ conversations
    def upsert_conversation(self, conversation_id: str, kind: str, name: str, members: list[str]) -> None:
        self._write(
            "INSERT INTO conversations(id, kind, name, members, updated_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET kind = excluded.kind, name = excluded.name, "
            "members = CASE WHEN excluded.members = '[]' THEN conversations.members ELSE excluded.members END, "
            "updated_at = excluded.updated_at",
            (conversation_id, kind, name, json.dumps(sorted(set(members))), time.time()),
        )

    def conversation(self, conversation_id: str) -> dict | None:
        row = self._one("SELECT * FROM conversations WHERE id = ?", (conversation_id,))
        if not row:
            return None
        out = dict(row)
        out["members"] = json.loads(out["members"])
        return out

    def conversations(self) -> list[dict]:
        rows = self._all("SELECT * FROM conversations ORDER BY name")
        out = []
        for row in rows:
            item = dict(row)
            item["members"] = json.loads(item["members"])
            out.append(item)
        return out

    @staticmethod
    def _visible(scope: str, alias: str = "c") -> str:
        if scope not in SCOPES:
            raise ValueError(f"unknown scope {scope!r}")
        return "1" if scope == "all" else f"{alias}.kind = 'public'"

    # ------------------------------------------------------------------ messages
    def upsert_message(
        self,
        conversation_id: str,
        ts: str,
        user_id: str | None,
        text: str,
        thread_ts: str | None = None,
        reply_count: int = 0,
        files: list[str] | None = None,
    ) -> bool:
        """Store a message once. Returns True when it is new or its content changed."""
        files_json = json.dumps(files or [])
        with self._lock:
            old = self._db.execute(
                "SELECT text, reply_count, files FROM messages WHERE conversation_id = ? AND ts = ?",
                (conversation_id, ts),
            ).fetchone()
            if old and old["text"] == text and old["reply_count"] == reply_count and old["files"] == files_json:
                return False
            self._db.execute(
                "INSERT INTO messages(conversation_id, ts, thread_ts, user_id, text, reply_count, files, stored_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(conversation_id, ts) DO UPDATE SET "
                "thread_ts = excluded.thread_ts, user_id = excluded.user_id, text = excluded.text, "
                "reply_count = excluded.reply_count, files = excluded.files, stored_at = excluded.stored_at",
                (conversation_id, ts, thread_ts, user_id, text, reply_count, files_json, time.time()),
            )
            doc_id = f"m:{conversation_id}:{ts}"
            self._db.execute("DELETE FROM search_index WHERE doc_id = ?", (doc_id,))
            if text:
                self._db.execute(
                    "INSERT INTO search_index(doc_id, conversation_id, ts, text) VALUES (?, ?, ?, ?)",
                    (doc_id, conversation_id, ts, text),
                )
            self._db.commit()
            return True

    def message_count(self) -> int:
        row = self._one("SELECT COUNT(*) AS n FROM messages")
        return int(row["n"]) if row else 0

    def recent_lines(self, scope: str, max_chars: int) -> str:
        """The latest messages as plain lines, newest last, within max_chars."""
        rows = self._all(
            "SELECT m.ts, m.text, m.user_id, c.name AS conv FROM messages m "
            f"JOIN conversations c ON c.id = m.conversation_id WHERE {self._visible(scope)} "
            "ORDER BY CAST(m.ts AS REAL) DESC LIMIT 2000"
        )
        lines: list[str] = []
        total = 0
        for row in rows:
            who = self.user_name(row["user_id"]) or "app"
            line = f"[{stamp_of(row['ts'])}] ({row['conv']}) {who}: {row['text'].replace(chr(10), ' ')}"
            total += len(line) + 1
            if total > max_chars:
                break
            lines.append(line)
        return "\n".join(reversed(lines))

    # ------------------------------------------------------------------ files
    def has_file(self, file_id: str) -> bool:
        return self._one("SELECT 1 FROM files WHERE id = ?", (file_id,)) is not None

    def save_file(
        self,
        file_id: str,
        conversation_id: str,
        message_ts: str | None,
        user_id: str | None,
        name: str,
        mimetype: str | None,
        size: int | None,
        text: str | None,
    ) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO files(id, conversation_id, message_ts, user_id, name, mimetype, size, text, stored_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET text = excluded.text, "
                "stored_at = excluded.stored_at",
                (file_id, conversation_id, message_ts, user_id, name, mimetype, size, text, time.time()),
            )
            doc_id = f"f:{file_id}"
            self._db.execute("DELETE FROM search_index WHERE doc_id = ?", (doc_id,))
            self._db.execute(
                "INSERT INTO search_index(doc_id, conversation_id, ts, text) VALUES (?, ?, ?, ?)",
                (doc_id, conversation_id, message_ts or "0", f"File {name}\n{text or ''}"),
            )
            self._db.commit()

    def file_texts(self, file_ids: list[str], limit_chars: int) -> list[tuple[str, str]]:
        out = []
        for file_id in file_ids:
            row = self._one("SELECT name, text FROM files WHERE id = ?", (file_id,))
            if row:
                out.append((row["name"], (row["text"] or "")[:limit_chars]))
        return out

    # ------------------------------------------------------------------ sign-ins
    def save_token(self, user_id: str, token_enc: str, scopes: str, team_id: str | None) -> None:
        self._write(
            "INSERT INTO user_tokens(user_id, token_enc, scopes, team_id, granted_at, failed_at) "
            "VALUES (?, ?, ?, ?, ?, NULL) ON CONFLICT(user_id) DO UPDATE SET token_enc = excluded.token_enc, "
            "scopes = excluded.scopes, team_id = excluded.team_id, granted_at = excluded.granted_at, failed_at = NULL",
            (user_id, token_enc, scopes, team_id, time.time()),
        )

    def tokens(self) -> list[dict]:
        return [dict(r) for r in self._all("SELECT * FROM user_tokens WHERE failed_at IS NULL")]

    def all_tokens(self) -> list[dict]:
        return [dict(r) for r in self._all("SELECT user_id, scopes, granted_at, failed_at FROM user_tokens")]

    def mark_token_failed(self, user_id: str) -> None:
        self._write("UPDATE user_tokens SET failed_at = ? WHERE user_id = ?", (time.time(), user_id))

    def delete_token(self, user_id: str) -> None:
        self._write("DELETE FROM user_tokens WHERE user_id = ?", (user_id,))

    # ------------------------------------------------------------------ sync state
    def last_ts(self, conversation_id: str) -> str | None:
        row = self._one("SELECT last_ts FROM sync_state WHERE conversation_id = ?", (conversation_id,))
        return row["last_ts"] if row else None

    def set_last_ts(self, conversation_id: str, ts: str) -> None:
        self._write(
            "INSERT INTO sync_state(conversation_id, last_ts) VALUES (?, ?) ON CONFLICT(conversation_id) "
            "DO UPDATE SET last_ts = CASE WHEN CAST(excluded.last_ts AS REAL) > CAST(sync_state.last_ts AS REAL) "
            "THEN excluded.last_ts ELSE sync_state.last_ts END",
            (conversation_id, ts),
        )

    # ------------------------------------------------------------------ digests
    def days_to_digest(self, limit: int = 200) -> list[tuple[str, str]]:
        """(conversation, day) pairs with messages stored after that day's digest was built."""
        rows = self._all(
            "SELECT m.conversation_id AS cid, date(CAST(m.ts AS REAL), 'unixepoch') AS day, "
            "MAX(m.stored_at) AS changed FROM messages m GROUP BY cid, day"
        )
        built = {
            (r["conversation_id"], r["day"]): r["built_at"]
            for r in self._all("SELECT conversation_id, day, built_at FROM digests")
        }
        out = [
            (r["cid"], r["day"])
            for r in rows
            if r["changed"] > built.get((r["cid"], r["day"]), 0)
        ]
        out.sort(key=lambda pair: pair[1])
        return out[:limit]

    def day_messages(self, conversation_id: str, day: str) -> list[dict]:
        rows = self._all(
            "SELECT * FROM messages WHERE conversation_id = ? AND date(CAST(ts AS REAL), 'unixepoch') = ? "
            "ORDER BY CAST(ts AS REAL)",
            (conversation_id, day),
        )
        out = []
        for row in rows:
            item = dict(row)
            item["files"] = json.loads(item["files"])
            out.append(item)
        return out

    def save_digest(self, conversation_id: str, day: str, data: dict, message_count: int) -> None:
        self._write(
            "INSERT INTO digests(conversation_id, day, data, message_count, built_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(conversation_id, day) DO UPDATE SET data = excluded.data, "
            "message_count = excluded.message_count, built_at = excluded.built_at",
            (conversation_id, day, json.dumps(data), message_count, time.time()),
        )

    def digests(
        self,
        scope: str,
        built_after: float = 0.0,
        since_day: str | None = None,
        conversation_id: str | None = None,
        limit: int = 500,
    ) -> list[dict]:
        sql = (
            "SELECT d.*, c.name AS conversation, c.kind FROM digests d "
            f"JOIN conversations c ON c.id = d.conversation_id WHERE {self._visible(scope)} AND d.built_at > ?"
        )
        args: list[Any] = [built_after]
        if since_day:
            sql += " AND d.day >= ?"
            args.append(since_day)
        if conversation_id:
            sql += " AND d.conversation_id = ?"
            args.append(conversation_id)
        sql += " ORDER BY d.built_at LIMIT ?"
        args.append(limit)
        out = []
        for row in self._all(sql, args):
            item = dict(row)
            item["data"] = json.loads(item["data"])
            out.append(item)
        return out

    # ------------------------------------------------------------------ knowledge
    def knowledge(self, scope: str, kind: str) -> list[dict]:
        rows = self._all(
            "SELECT data FROM knowledge WHERE scope = ? AND kind = ? ORDER BY key", (scope, kind)
        )
        return [json.loads(r["data"]) for r in rows]

    def replace_knowledge(self, scope: str, kind: str, items: list[dict]) -> None:
        with self._lock:
            self._db.execute("DELETE FROM knowledge WHERE scope = ? AND kind = ?", (scope, kind))
            for item in items:
                name = str(item.get("name") or "").strip()
                if not name:
                    continue
                self._db.execute(
                    "INSERT OR REPLACE INTO knowledge(scope, kind, key, data, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (scope, kind, key_of(name), json.dumps(item), time.time()),
                )
            self._db.commit()

    # ------------------------------------------------------------------ search
    def search(
        self,
        query: str,
        scope: str,
        person: str | None = None,
        since_day: str | None = None,
        limit: int = 25,
    ) -> list[dict]:
        """Full-text search over messages and files the scope may see.

        Repeats are folded: the same words from the same person (a message pasted into
        several chats) come back once, with every conversation it was seen in.
        """
        words = [w.strip(".'-") for w in _WORD.findall(query.lower())]
        words = [w for w in words if len(w) > 1 and w not in _STOP]
        if not words:
            return []
        match = " OR ".join('"' + w.replace('"', "") + '"' for w in words)
        sql = (
            "SELECT s.doc_id, s.conversation_id, s.ts, s.text, c.name AS conv, c.kind, bm25(search_index) AS rank "
            "FROM search_index s JOIN conversations c ON c.id = s.conversation_id "
            f"WHERE search_index MATCH ? AND {self._visible(scope)}"
        )
        args: list[Any] = [match]
        if since_day:
            sql += " AND CAST(s.ts AS REAL) >= ?"
            args.append(datetime.strptime(since_day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
        sql += " ORDER BY rank LIMIT ?"
        args.append(limit * 4)
        rows = self._all(sql, args)

        person_ids: set[str] | None = None
        if person:
            pattern = f"%{person.strip().lower()}%"
            person_ids = {
                r["id"]
                for r in self._all(
                    "SELECT id FROM users WHERE lower(name) LIKE ? OR lower(real_name) LIKE ?",
                    (pattern, pattern),
                )
            }

        results: list[dict] = []
        seen: dict[tuple[str, str], dict] = {}
        for row in rows:
            doc_id = row["doc_id"]
            if doc_id.startswith("m:"):
                msg = self._one(
                    "SELECT user_id, thread_ts FROM messages WHERE conversation_id = ? AND ts = ?",
                    (row["conversation_id"], row["ts"]),
                )
                user_id = msg["user_id"] if msg else None
                kind = "message"
                in_thread = bool(msg and msg["thread_ts"] and msg["thread_ts"] != row["ts"])
            else:
                file_row = self._one("SELECT user_id FROM files WHERE id = ?", (doc_id[2:],))
                user_id = file_row["user_id"] if file_row else None
                kind = "file"
                in_thread = False
            if person_ids is not None and user_id not in person_ids:
                continue
            fold = (user_id or "", _norm(row["text"])[:300])
            if fold in seen:
                if row["conv"] not in seen[fold]["also_in"] and row["conv"] != seen[fold]["conversation"]:
                    seen[fold]["also_in"].append(row["conv"])
                continue
            item = {
                "type": kind,
                "conversation": row["conv"],
                "conversation_kind": row["kind"],
                "when": stamp_of(row["ts"]) if row["ts"] and row["ts"] != "0" else None,
                "who": self.user_name(user_id) or "app",
                "text": row["text"][:1200],
                "in_thread": in_thread,
                "also_in": [],
            }
            seen[fold] = item
            results.append(item)
            if len(results) >= limit:
                break
        return results

    def person_activity(self, user_id: str, scope: str) -> list[dict]:
        rows = self._all(
            "SELECT c.name AS conv, c.kind, COUNT(*) AS n, MAX(CAST(m.ts AS REAL)) AS last "
            "FROM messages m JOIN conversations c ON c.id = m.conversation_id "
            f"WHERE m.user_id = ? AND {self._visible(scope)} GROUP BY c.id ORDER BY n DESC LIMIT 30",
            (user_id,),
        )
        return [
            {
                "conversation": r["conv"],
                "kind": r["kind"],
                "messages": r["n"],
                "last": stamp_of(str(r["last"])),
            }
            for r in rows
        ]
