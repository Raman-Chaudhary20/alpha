"""Claude on Amazon Bedrock (Alpha's AWS account): structured notes and answers.

Answers are given by Claude with tools over Alpha's memory (the project and people
register, day summaries, and a full-text search of every message and file). The scope
("all" for admins in a private place, "public" otherwise) is fixed by the code before
the model runs, and every tool reads through it: the model cannot widen what it sees.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from app.config import Settings
from app.services.store import Store, key_of

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 8


def clean_reply(text: str) -> str:
    """Strip markdown formatting from an AI reply for plain-text delivery in Slack."""
    text = re.sub(r"^\s*\*\s+", "- ", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"__(.+?)__", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"(?<!\w)\*(\S(?:.*?\S)?)\*(?!\w)", r"\1", text)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"`{1,3}", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class Bedrock:
    """A thin async wrapper over the Bedrock Converse API."""

    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self._settings = settings
        self._client = client

    def _runtime(self) -> Any:
        if self._client is None:
            import boto3
            from botocore.config import Config

            session = boto3.Session(
                profile_name=self._settings.aws_profile or None,
                region_name=self._settings.aws_region,
            )
            self._client = session.client(
                "bedrock-runtime",
                config=Config(read_timeout=180, retries={"max_attempts": 6, "mode": "adaptive"}),
            )
        return self._client

    async def converse(self, **kwargs: Any) -> dict:
        return await asyncio.to_thread(self._runtime().converse, **kwargs)

    async def structured(
        self,
        model: str,
        system: str,
        prompt: str,
        tool_name: str,
        description: str,
        schema: dict,
        max_tokens: int = 4000,
    ) -> dict:
        """Ask the model to answer by filling in one tool's input, and return that input."""
        resp = await self.converse(
            modelId=model,
            system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            toolConfig={
                "tools": [
                    {"toolSpec": {"name": tool_name, "description": description, "inputSchema": {"json": schema}}}
                ],
                "toolChoice": {"tool": {"name": tool_name}},
            },
            inferenceConfig={"maxTokens": max_tokens},
        )
        for block in resp["output"]["message"]["content"]:
            if "toolUse" in block:
                return block["toolUse"]["input"]
        raise ValueError("the model did not return the structured answer")


# ---------------------------------------------------------------------------
# Answering
# ---------------------------------------------------------------------------

_TOOLS = [
    {
        "name": "list_projects",
        "description": (
            "The register of projects Alpha has worked out from the conversations: each with "
            "status, summary, the people on it and their roles, and when it was last discussed. "
            "Use for any question about what work is going on, how many projects, or who works on what."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Only projects with this status: proposed, active, blocked, done or unknown. Leave out for all.",
                }
            },
        },
    },
    {
        "name": "get_project",
        "description": "One project in full, with the recent day summaries that mention it.",
        "schema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "The project's name or a name people use for it."}},
            "required": ["name"],
        },
    },
    {
        "name": "list_people",
        "description": "Everyone in the workspace, with their title, what they work on, and whether Alpha reads their chats.",
        "schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_person",
        "description": "One person: what they work on, their role, and where they talk most.",
        "schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "name": "search_messages",
        "description": (
            "Full-text search of every message and shared file Alpha may show for this question. "
            "Repeats are folded. Use for specific facts, quotes, dates, numbers and decisions."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Key words to look for."},
                "person": {"type": "string", "description": "Only messages written by this person."},
                "since": {"type": "string", "description": "Only from this day on, YYYY-MM-DD."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "recent_activity",
        "description": "Summaries of each conversation's recent days: what was discussed, decided and assigned.",
        "schema": {
            "type": "object",
            "properties": {"days": {"type": "integer", "description": "How many days back, 1 to 60. Default 7."}},
        },
    },
]

_SYSTEM = """\
Your name is Alpha. You are the memory of this organization's Slack workspace: you have read its \
conversations, removed the repeats and kept a register of its projects and people.

Today is {today}. You are answering {asker}.
What you may use for this answer: {scope_words}
Alpha holds {messages} messages from {conversations} conversations; {readers} of {people} people \
have let Alpha read their chats. Last read from Slack: {last_sync}.

How to answer:
- Look things up with the tools before answering. For questions about projects, who does what, or \
how much work is going on, start with list_projects; check details with get_project or search_messages.
- Say how many when asked how many, then list them. For each project give its status and the people \
on it with their role, in one line each.
- Name people and say where and when something was said when it matters ("in the DM between A and B \
on 12 Nov").
- Only state what the conversations say. If they do not say, answer that you do not know.
- Plain text for Slack: no ** or #, no tables, no backticks. Use lines starting with "- " for lists.
- Be short: a sentence or two, or a list when there are several things."""


class Brain:
    """Answers questions from Alpha's memory, within a scope set by the caller."""

    def __init__(self, settings: Settings, store: Store, bedrock: Bedrock) -> None:
        self._settings = settings
        self._store = store
        self._bedrock = bedrock

    async def ask_bot(self, question: str, scope: str = "public", asker: str | None = None) -> str:
        if scope not in ("all", "public"):
            raise ValueError("scope must be 'all' or 'public'")
        system = self._system(scope, asker)
        messages: list[dict] = [{"role": "user", "content": [{"text": question}]}]
        tool_config = {
            "tools": [
                {"toolSpec": {"name": t["name"], "description": t["description"], "inputSchema": {"json": t["schema"]}}}
                for t in _TOOLS
            ]
        }
        for _ in range(MAX_TOOL_ROUNDS):
            resp = await self._bedrock.converse(
                modelId=self._settings.bedrock_answer_model,
                system=[{"text": system}],
                messages=messages,
                toolConfig=tool_config,
                inferenceConfig={"maxTokens": 2000},
            )
            message = resp["output"]["message"]
            messages.append(message)
            uses = [b["toolUse"] for b in message["content"] if "toolUse" in b]
            if not uses:
                text = "\n".join(b["text"] for b in message["content"] if "text" in b)
                return clean_reply(text) or "I don't know."
            results = []
            for use in uses:
                try:
                    output = self.run_tool(use["name"], use.get("input") or {}, scope)
                    content = [{"json": {"result": output}}]
                    status = "success"
                except Exception as exc:  # the model reads the error and can try again
                    logger.warning("Tool %s failed: %s", use["name"], exc)
                    content = [{"text": f"error: {exc}"}]
                    status = "error"
                results.append({"toolResult": {"toolUseId": use["toolUseId"], "content": content, "status": status}})
            messages.append({"role": "user", "content": results})
        return "I could not finish looking that up. Please ask again more narrowly."

    def _system(self, scope: str, asker: str | None) -> str:
        users = self._store.users()
        last = self._store.get_meta("last_harvest_at")
        last_sync = (
            datetime.fromtimestamp(float(last), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if last else "never"
        )
        scope_words = (
            "every conversation: public and private channels, DMs and group DMs."
            if scope == "all"
            else "public channels only. Private channels, DMs and group DMs are not available for this "
            "answer; if the question needs them, say that only an admin can ask that, in a direct message to Alpha."
        )
        return _SYSTEM.format(
            today=datetime.now(tz=timezone.utc).strftime("%Y-%m-%d"),
            asker=asker or "a member of the workspace",
            scope_words=scope_words,
            messages=self._store.message_count(),
            conversations=len(self._store.conversations()),
            readers=len(self._store.tokens()),
            people=len(users),
            last_sync=last_sync,
        )

    # ------------------------------------------------------------------ tools
    def run_tool(self, name: str, args: dict, scope: str) -> Any:
        store = self._store
        if name == "list_projects":
            projects = store.knowledge(scope, "project")
            status = (args.get("status") or "").strip().lower()
            if status:
                projects = [p for p in projects if str(p.get("status", "")).lower() == status]
            return {"count": len(projects), "projects": projects}
        if name == "get_project":
            project = self._find(store.knowledge(scope, "project"), args.get("name", ""))
            if not project:
                return {"found": False, "projects": [p.get("name") for p in store.knowledge(scope, "project")]}
            names = {key_of(project["name"]), *(key_of(a) for a in project.get("aliases") or [])}
            mentions = []
            for digest in reversed(store.digests(scope, limit=2000)):
                listed = {key_of(p.get("name", "")) for p in digest["data"].get("projects") or []}
                text = digest["data"].get("summary", "").lower()
                if listed & names or any(n and n in text for n in names):
                    mentions.append(
                        {
                            "conversation": digest["conversation"],
                            "day": digest["day"],
                            "summary": digest["data"].get("summary"),
                            "updates": [
                                p for p in digest["data"].get("projects") or [] if key_of(p.get("name", "")) in names
                            ],
                        }
                    )
                if len(mentions) >= 15:
                    break
            return {"found": True, "project": project, "mentions": mentions}
        if name == "list_people":
            people = {key_of(p.get("name", "")): p for p in store.knowledge(scope, "person")}
            readers = {t["user_id"] for t in store.tokens()}
            out = []
            for user in store.users():
                known = people.get(key_of(user["name"])) or people.get(key_of(user["real_name"] or "")) or {}
                out.append(
                    {
                        "name": user["name"],
                        "real_name": user["real_name"],
                        "title": user["title"],
                        "works_on": known.get("works_on", []),
                        "role": known.get("role"),
                        "alpha_reads_their_chats": user["id"] in readers,
                    }
                )
            return {"count": len(out), "people": out}
        if name == "get_person":
            wanted = key_of(args.get("name", ""))
            user = next(
                (
                    u
                    for u in store.users()
                    if wanted and (wanted in key_of(u["name"]) or wanted in key_of(u["real_name"] or ""))
                ),
                None,
            )
            known = self._find(store.knowledge(scope, "person"), args.get("name", ""))
            if not user and not known:
                return {"found": False}
            return {
                "found": True,
                "person": known or {"name": user["name"]},
                "title": user["title"] if user else None,
                "talks_in": store.person_activity(user["id"], scope) if user else [],
            }
        if name == "search_messages":
            return {
                "results": store.search(
                    args.get("query", ""), scope, person=args.get("person") or None, since_day=args.get("since") or None
                )
            }
        if name == "recent_activity":
            days = max(1, min(60, int(args.get("days") or 7)))
            since = (datetime.now(tz=timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
            digests = store.digests(scope, since_day=since, limit=2000)
            digests.sort(key=lambda d: d["day"], reverse=True)
            return {
                "days": [
                    {
                        "conversation": d["conversation"],
                        "day": d["day"],
                        "summary": d["data"].get("summary"),
                        "decisions": d["data"].get("decisions"),
                        "tasks": d["data"].get("tasks"),
                    }
                    for d in digests[:60]
                ]
            }
        raise ValueError(f"unknown tool {name}")

    @staticmethod
    def _find(items: list[dict], name: str) -> dict | None:
        wanted = key_of(name)
        if not wanted:
            return None
        for item in items:
            names = [key_of(item.get("name", ""))] + [key_of(a) for a in item.get("aliases") or []]
            if wanted in names:
                return item
        for item in items:
            names = [key_of(item.get("name", ""))] + [key_of(a) for a in item.get("aliases") or []]
            if any(wanted in n or (n and n in wanted) for n in names):
                return item
        return None
