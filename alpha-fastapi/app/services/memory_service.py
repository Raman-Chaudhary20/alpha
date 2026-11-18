"""Turns stored chats into Alpha's structured memory.

Two steps, both incremental:
1. Day summaries: each conversation's day is read once (and again only when a message of
   that day changes) by a small fast model, which writes what was discussed, the projects
   named, decisions and tasks.
2. The register: new day summaries are merged into one register of projects and people,
   by a stronger model. Duplicates are merged here: the same project under two names, or
   the same news from two conversations, ends as one entry with its aliases.

Two registers are kept: "all" (every conversation, for admins) and "public" (built only
from public channels, for everyone else), so a private DM never leaks into an answer for
someone who may not see it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone

from app.config import Settings
from app.services.bedrock_service import Bedrock
from app.services.store import SCOPES, Store, key_of, stamp_of

logger = logging.getLogger(__name__)

TRANSCRIPT_CHARS = 60_000
FILE_EXCERPT_CHARS = 3_000
REGISTER_BATCH = 40

DIGEST_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": "Two to five plain sentences: what was discussed, asked, decided and agreed, naming people.",
        },
        "projects": {
            "type": "array",
            "description": "Every project, client engagement, product or initiative the messages talk about.",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "The name people use for it."},
                    "status": {"type": "string", "enum": ["proposed", "active", "blocked", "done", "unknown"]},
                    "update": {"type": "string", "description": "What these messages say about it."},
                    "people": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "role": {"type": "string", "description": "What they do on it, e.g. leads, builds, reviews, client."},
                            },
                            "required": ["name"],
                        },
                    },
                },
                "required": ["name", "status"],
            },
        },
        "decisions": {"type": "array", "items": {"type": "string"}},
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "owner": {"type": "string"},
                    "task": {"type": "string"},
                    "due": {"type": "string"},
                },
                "required": ["task"],
            },
        },
    },
    "required": ["summary", "projects"],
}

REGISTER_SCHEMA = {
    "type": "object",
    "properties": {
        "projects": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "aliases": {"type": "array", "items": {"type": "string"}},
                    "status": {"type": "string", "enum": ["proposed", "active", "blocked", "done", "unknown"]},
                    "summary": {"type": "string", "description": "What it is and where it stands, in two or three sentences."},
                    "people": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"name": {"type": "string"}, "role": {"type": "string"}},
                            "required": ["name"],
                        },
                    },
                    "last_update": {"type": "string", "description": "YYYY-MM-DD of the latest news."},
                    "conversations": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name", "status", "summary", "people"],
            },
        },
        "people": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "As listed among the workspace's people."},
                    "role": {"type": "string", "description": "Their job, as the conversations show it."},
                    "works_on": {"type": "array", "items": {"type": "string"}, "description": "Project names."},
                    "summary": {"type": "string"},
                },
                "required": ["name", "works_on"],
            },
        },
    },
    "required": ["projects", "people"],
}

_DIGEST_SYSTEM = """\
You read one day of one Slack conversation of an organization and write down what it holds, for the \
organization's memory. Record only what the messages say; never guess. A project is a named piece of \
work with a goal: a product, a feature, a client engagement, a campaign, an internal initiative. Use \
the names people use. Small talk carries no project. People's names as written in the messages."""

_REGISTER_SYSTEM = """\
You keep an organization's register of projects and people, built from summaries of its Slack \
conversations. You are given the current register and new day summaries. Return the whole updated \
register.

Rules:
- Keep every project and person already in the register. Remove one only when it is the same as \
another; then keep one entry and put the other names in its aliases.
- The same project is often discussed in several conversations and under slightly different names \
(a DM and a channel, "Website revamp" and "new site"): merge them into one entry.
- Update status, summary, people and last_update from the newest news. A project's people are those \
who work on it, with their role.
- People: use the names in the workspace's list of people. works_on lists project names from the register.
- Record only what the summaries say."""


class MemoryBuilder:
    def __init__(self, settings: Settings, store: Store, bedrock: Bedrock) -> None:
        self._settings = settings
        self._store = store
        self._bedrock = bedrock
        self._lock = asyncio.Lock()

    async def build(self, max_days: int = 200) -> dict:
        """Summarize changed days, then fold new summaries into both registers."""
        async with self._lock:
            started = time.time()
            digested = await self.digest_days(max_days)
            merged = {scope: await self.update_register(scope) for scope in SCOPES}
            took = round(time.time() - started, 1)
            if digested or any(merged.values()):
                logger.info("Memory build: %d day summaries, register updates %s, %ss", digested, merged, took)
            return {"day_summaries": digested, "register_updates": merged, "seconds": took}

    # ------------------------------------------------------------------ day summaries
    def transcript(self, conversation_id: str, day: str) -> tuple[str, int]:
        messages = self._store.day_messages(conversation_id, day)
        lines = []
        for msg in messages:
            who = self._store.user_name(msg["user_id"]) or "app"
            when = stamp_of(msg["ts"])[11:]
            reply = " (in a thread)" if msg["thread_ts"] and msg["thread_ts"] != msg["ts"] else ""
            lines.append(f"[{when}] {who}{reply}: {msg['text']}")
            for name, text in self._store.file_texts(msg["files"], FILE_EXCERPT_CHARS):
                lines.append(f"    shared file {name}" + (f":\n    {text}" if text else ""))
        return "\n".join(lines), len(messages)

    async def digest_days(self, max_days: int) -> int:
        done = 0
        for conversation_id, day in self._store.days_to_digest(max_days):
            conversation = self._store.conversation(conversation_id) or {"name": conversation_id, "kind": "public"}
            transcript, count = self.transcript(conversation_id, day)
            if not transcript.strip():
                continue
            parts = [transcript[i : i + TRANSCRIPT_CHARS] for i in range(0, len(transcript), TRANSCRIPT_CHARS)]
            merged: dict = {"summary": "", "projects": [], "decisions": [], "tasks": []}
            try:
                for part in parts:
                    data = await self._bedrock.structured(
                        model=self._settings.bedrock_digest_model,
                        system=_DIGEST_SYSTEM,
                        prompt=f"Conversation: {conversation['name']}\nDay: {day}\n\n<messages>\n{part}\n</messages>",
                        tool_name="record_day",
                        description="Record what this day of the conversation holds.",
                        schema=DIGEST_SCHEMA,
                        max_tokens=3000,
                    )
                    merged["summary"] = (merged["summary"] + " " + str(data.get("summary") or "")).strip()
                    for key in ("projects", "decisions", "tasks"):
                        merged[key].extend(data.get(key) or [])
            except Exception:
                logger.exception("Day summary failed for %s %s; will retry", conversation["name"], day)
                continue
            self._store.save_digest(conversation_id, day, merged, count)
            done += 1
        return done

    # ------------------------------------------------------------------ register
    async def update_register(self, scope: str) -> int:
        """Fold day summaries built since the last update into the scope's register."""
        meta_key = f"register_built_{scope}"
        batches = 0
        while True:
            since = float(self._store.get_meta(meta_key, "0") or 0)
            new = self._store.digests(scope, built_after=since, limit=REGISTER_BATCH)
            if not new:
                return batches
            current_projects = self._store.knowledge(scope, "project")
            current_people = self._store.knowledge(scope, "person")
            people = [
                {"name": u["name"], "real_name": u["real_name"], "title": u["title"]} for u in self._store.users()
            ]
            updates = [
                {
                    "conversation": d["conversation"],
                    "day": d["day"],
                    "summary": d["data"].get("summary"),
                    "projects": d["data"].get("projects"),
                    "decisions": d["data"].get("decisions"),
                    "tasks": d["data"].get("tasks"),
                }
                for d in new
            ]
            prompt = (
                f"Today: {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d')}\n\n"
                f"<workspace_people>\n{json.dumps(people, ensure_ascii=False)}\n</workspace_people>\n\n"
                f"<current_register>\n{json.dumps({'projects': current_projects, 'people': current_people}, ensure_ascii=False)}\n"
                f"</current_register>\n\n<new_day_summaries>\n{json.dumps(updates, ensure_ascii=False)}\n</new_day_summaries>"
            )
            try:
                data = await self._bedrock.structured(
                    model=self._settings.bedrock_answer_model,
                    system=_REGISTER_SYSTEM,
                    prompt=prompt,
                    tool_name="save_register",
                    description="Save the whole updated register of projects and people.",
                    schema=REGISTER_SCHEMA,
                    max_tokens=16000,
                )
            except Exception:
                logger.exception("Register update (%s) failed; will retry", scope)
                return batches
            projects = keep_unmentioned(current_projects, data.get("projects") or [])
            people_out = keep_unmentioned(current_people, data.get("people") or [])
            self._store.replace_knowledge(scope, "project", projects)
            self._store.replace_knowledge(scope, "person", people_out)
            self._store.set_meta(meta_key, repr(max(d["built_at"] for d in new)))
            batches += 1


def keep_unmentioned(before: list[dict], after: list[dict]) -> list[dict]:
    """A model rewriting a register can drop entries by mistake. Keep any earlier entry
    whose name is neither in the new register nor an alias of one of its entries."""
    named = set()
    for item in after:
        named.add(key_of(str(item.get("name") or "")))
        named.update(key_of(str(a)) for a in item.get("aliases") or [])
    kept = list(after)
    for item in before:
        if key_of(str(item.get("name") or "")) not in named:
            kept.append(item)
    return kept
