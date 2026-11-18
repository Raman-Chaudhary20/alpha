"""Slack service: the Bolt Socket Mode bot, live message capture, and who may see what.

Reading the workspace is done by the Harvester (bot token plus every person who signed
in); answering by the Brain on Bedrock. This class decides the scope of each answer:

- a question in a channel (@alpha ...) is answered from public channels only, whoever
  asks, because everyone in that channel reads the answer;
- a question in a DM to Alpha is answered from every conversation when the asker is an
  admin (a Slack workspace admin or owner, or listed in ADMIN_USER_IDS), and from public
  channels only otherwise.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp
from slack_sdk.errors import SlackApiError

from app.config import Settings
from app.core.secrets import Secrets
from app.services.bedrock_service import Brain
from app.services.file_service import FileService
from app.services.harvest_service import Harvester, error_code, make_client
from app.services.store import Store

logger = logging.getLogger(__name__)

KIND_OF_CHANNEL_TYPE = {"channel": "public", "group": "private", "im": "im", "mpim": "mpim"}

HELP = (
    "Ask me anything about the work going on, e.g. what projects are active and who is on them.\n"
    "- connect: let me read your Slack conversations, so I know what you work on\n"
    "- disconnect: stop me reading your conversations\n"
    "Admins get answers from private chats only here, in this direct message."
)


class SlackService:
    """Handles the Bolt socket-mode bot and decides the scope of each answer."""

    def __init__(
        self,
        settings: Settings,
        file_service: FileService,
        store: Store,
        brain: Brain,
        harvester: Harvester,
        secrets: Secrets,
        bot_client: Any | None = None,
    ) -> None:
        self._settings = settings
        self._file_service = file_service
        self._store = store
        self._brain = brain
        self._harvester = harvester
        self._secrets = secrets
        self._web_client = bot_client or make_client(settings.slack_bot_token)
        self._bolt_app = AsyncApp(token=settings.slack_bot_token, signing_secret=None)
        self._socket_handler: AsyncSocketModeHandler | None = None
        self._bot_user_id: str | None = None
        self._sync_lock = asyncio.Lock()
        self._members_lock = asyncio.Lock()

        self._bolt_app.event("app_mention")(self._handle_mention)
        self._bolt_app.event("message")(self._handle_message)

    @property
    def web_client(self) -> Any:
        return self._web_client

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        try:
            auth = await asyncio.wait_for(self._web_client.auth_test(), timeout=10.0)
            self._bot_user_id = auth["user_id"]
            self._store.set_meta("bot_user_id", auth["user_id"])
            if auth.get("team_id"):
                self._store.set_meta("team_id", auth["team_id"])
            logger.info("Slack bot authenticated as %s (%s)", auth.get("user"), self._bot_user_id)
        except Exception as exc:
            logger.warning("Slack auth_test failed or timed out: %s; running in offline/API mode", exc)
            return
        try:
            self._socket_handler = AsyncSocketModeHandler(app=self._bolt_app, app_token=self._settings.slack_app_token)
            await asyncio.wait_for(self._socket_handler.connect_async(), timeout=15.0)
            logger.info("Slack bot is running (Socket Mode)")
            asyncio.create_task(self._initial_sync())
        except Exception as exc:
            logger.warning("Slack Socket Mode connection failed: %s", exc)

    async def _initial_sync(self) -> None:
        await self.sync_members()
        await self.sync_slack()

    async def stop(self) -> None:
        if self._socket_handler:
            try:
                await self._socket_handler.close_async()
            except Exception:
                logger.exception("Error stopping Slack socket handler")

    # ------------------------------------------------------------------ who may see what
    def is_admin(self, user_id: str | None) -> bool:
        if not user_id:
            return False
        return user_id in self._settings.admin_ids or self._store.is_workspace_admin(user_id)

    def scope_for(self, user_id: str | None, is_dm: bool) -> str:
        return "all" if (is_dm and self.is_admin(user_id)) else "public"

    async def user_name(self, user_id: str) -> str:
        name = self._store.user_name(user_id)
        if name:
            return name
        try:
            resp = await self._web_client.users_info(user=user_id)
            self._store.upsert_user(resp["user"])
        except Exception:
            logger.warning("Could not resolve user %s", user_id)
        return self._store.user_name(user_id) or user_id

    # ------------------------------------------------------------------ events
    async def _handle_mention(self, event: dict, say: Any) -> None:
        """@Alpha in a channel: answered from public channels only, in the thread."""
        question = re.sub(r"<@\w+>", "", event.get("text", "")).strip()
        if not question:
            await say(text=HELP, thread_ts=event.get("ts"))
            return
        user_id = event.get("user", "")
        who = await self.user_name(user_id)
        channel = event.get("channel", "slack")
        logger.info("Mention from %s in %s", who, channel)
        try:
            answer = await self._brain.ask_bot(question, scope="public", asker=who)
            if self.is_admin(user_id):
                answer += "\n\n(Answered from public channels only. For answers that use private chats, ask me in a direct message.)"
        except Exception as exc:
            logger.exception("Error handling mention")
            answer = f"Sorry, something went wrong: {exc}"
        await say(text=answer, thread_ts=event.get("ts"))
        self._file_service.save_conversation(f"#{channel}", who, question, answer)

    async def _handle_message(self, event: dict, say: Any = None) -> None:
        """Every message the bot can see: store it; a DM to Alpha is a question."""
        if event.get("bot_id") or event.get("subtype"):
            return
        text = (event.get("text") or "").strip()
        if not text:
            return
        user_id = event.get("user", "")
        channel_id = event.get("channel", "")
        channel_type = event.get("channel_type") or ("im" if str(channel_id).startswith("D") else "channel")
        is_dm = channel_type == "im"

        if not is_dm:
            if self._bot_user_id and f"<@{self._bot_user_id}>" in text:
                return  # answered by app_mention
            await self._capture(event, channel_type)
            return

        if say is None:
            return
        who = await self.user_name(user_id)
        question = re.sub(r"<@\w+>", "", text).strip()
        command = question.lower().strip(" .!")
        if command in ("connect", "sign in", "signin"):
            await say(text=self.connect_text(), thread_ts=event.get("thread_ts"))
            return
        if command in ("disconnect", "sign out", "signout"):
            await say(text=await self.disconnect(user_id), thread_ts=event.get("thread_ts"))
            return
        if command in ("help", "?"):
            await say(text=HELP, thread_ts=event.get("thread_ts"))
            return
        try:
            answer = await self._brain.ask_bot(question, scope=self.scope_for(user_id, True), asker=who)
        except Exception as exc:
            logger.exception("Error answering direct message")
            answer = f"Sorry, something went wrong: {exc}"
        await say(text=answer, thread_ts=event.get("thread_ts"))
        self._file_service.save_conversation(f"DM:{who}", who, question, answer)

    async def _capture(self, event: dict, channel_type: str) -> None:
        """Store a live channel message at once; the next harvest fills in files and names."""
        channel_id = event.get("channel", "")
        if not self._store.conversation(channel_id):
            kind = KIND_OF_CHANNEL_TYPE.get(channel_type, "private")
            self._store.upsert_conversation(channel_id, kind, channel_id, [])
        text = await self._harvester.clean(self._web_client, event.get("text") or "")
        self._store.upsert_message(
            channel_id,
            event["ts"],
            event.get("user"),
            text,
            thread_ts=event.get("thread_ts"),
            files=[f["id"] for f in event.get("files") or [] if f.get("id")],
        )

    # ------------------------------------------------------------------ sign-in
    def connect_text(self) -> str:
        if not (self._settings.public_url and self._settings.slack_client_id and self._secrets.ready):
            return "Signing in is not set up yet. Ask the person who runs Alpha to set PUBLIC_URL, SLACK_CLIENT_ID and TOKEN_KEY."
        link = self._settings.public_url.rstrip("/") + "/api/v1/slack/connect"
        return (
            f"Open this link and click Allow: {link}\n"
            "I will then read your Slack conversations, including your DMs, so I can tell your admins "
            "what work is going on. Say disconnect at any time to stop."
        )

    async def disconnect(self, user_id: str) -> str:
        row = next((t for t in self._store.tokens() if t["user_id"] == user_id), None)
        if row is None and not any(t["user_id"] == user_id for t in self._store.all_tokens()):
            return "I was not reading your conversations."
        if row is not None:
            token = self._secrets.decrypt(row["token_enc"]) if self._secrets.ready else None
            if token:
                try:
                    await make_client(token).auth_revoke()
                except SlackApiError as exc:
                    logger.warning("Revoking the sign-in of %s: %s", user_id, error_code(exc))
        self._store.delete_token(user_id)
        return "Done. I no longer read your conversations. What I had already read stays in my memory."

    async def invite(self) -> dict:
        """DM every person who has not signed in, with the sign-in link."""
        connected = {t["user_id"] for t in self._store.tokens()}
        sent, failed = [], []
        for user in self._store.users():
            if user["id"] in connected or user["id"] == self._bot_user_id:
                continue
            try:
                opened = await self._web_client.conversations_open(users=user["id"])
                await self._web_client.chat_postMessage(
                    channel=opened["channel"]["id"],
                    text="Hi, I'm Alpha, the team's memory bot. " + self.connect_text(),
                )
                sent.append(user["name"])
            except SlackApiError as exc:
                failed.append({"name": user["name"], "error": error_code(exc)})
        return {"sent": sent, "failed": failed}

    # ------------------------------------------------------------------ sync
    async def sync_slack(self) -> None:
        """Read everything new through the bot and every signed-in person."""
        if self._sync_lock.locked():
            logger.debug("Slack sync already in progress, skipping")
            return
        async with self._sync_lock:
            try:
                await self._harvester.harvest()
                self._store.set_meta("last_harvest_at", repr(time.time()))
            except Exception:
                logger.exception("Slack sync failed")

    async def sync_members(self) -> None:
        if self._members_lock.locked():
            return
        async with self._members_lock:
            try:
                await self._harvester.sync_users()
                lines = []
                for user in self._store.users():
                    line = f"{user['name']} ({user['real_name']})"
                    if user["title"]:
                        line += f" - {user['title']}"
                    lines.append(line)
                self._file_service.members_file.write_text("\n".join(lines), encoding="utf-8")
            except Exception:
                logger.exception("Member sync failed")
