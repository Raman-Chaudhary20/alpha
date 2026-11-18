"""Slack service — channel sync, member sync, and Bolt socket-mode bot."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_sdk.web.async_client import AsyncWebClient

from app.config import Settings
from app.services.file_service import FileService
from app.services.groq_service import GroqService

logger = logging.getLogger(__name__)

# cache TTL constants
_CHANNEL_CACHE_SECONDS = 600  # 10 min
_USER_DISPLAY_NAME_FALLBACK = "unknown"


class SlackService:
    """Handles all Slack API interactions and the Bolt socket-mode bot."""

    def __init__(
        self,
        settings: Settings,
        file_service: FileService,
        groq_service: GroqService,
    ) -> None:
        self._settings = settings
        self._file_service = file_service
        self._groq_service = groq_service

        self._web_client = AsyncWebClient(token=settings.slack_bot_token, timeout=10)
        self._bolt_app = AsyncApp(
            token=settings.slack_bot_token,
            signing_secret=None,  # socket mode — no signing secret needed
        )
        self._socket_handler: AsyncSocketModeHandler | None = None

        self._bot_user_id: str | None = None
        self._user_cache: dict[str, str] = {}
        self._channel_cache: list[dict] = []
        self._channel_cache_ts: float = 0.0

        self._sync_lock = asyncio.Lock()
        self._members_lock = asyncio.Lock()

        # Register event handlers
        self._bolt_app.event("app_mention")(self._handle_mention)
        self._bolt_app.event("message")(self._handle_message)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Authenticate and start the Bolt socket-mode handler."""
        try:
            auth = await asyncio.wait_for(self._web_client.auth_test(), timeout=10.0)
            self._bot_user_id = auth["user_id"]
            logger.info("Slack bot authenticated as %s (%s)", auth.get("user"), self._bot_user_id)
        except Exception as exc:
            logger.warning("Slack auth_test failed or timed out: %s; running in offline/API mode", exc)
            return

        try:
            self._socket_handler = AsyncSocketModeHandler(
                app=self._bolt_app,
                app_token=self._settings.slack_app_token,
            )
            await asyncio.wait_for(self._socket_handler.connect_async(), timeout=15.0)
            logger.info("⚡ Slack bot is running (Socket Mode)")

            # Initial background sync: members first (fills user cache), then messages
            asyncio.create_task(self._initial_sync())
        except Exception as exc:
            logger.warning("Slack Socket Mode connection failed: %s", exc)

    async def _initial_sync(self) -> None:
        """Run initial sync non-blockingly on startup."""
        try:
            await self.sync_members()
        except Exception:
            logger.exception("Initial Slack member sync error")
        try:
            await self.sync_slack()
        except Exception:
            logger.exception("Initial Slack message sync error")

    async def stop(self) -> None:
        """Gracefully stop the socket-mode handler."""
        if self._socket_handler:
            try:
                await self._socket_handler.close_async()
            except Exception:
                logger.exception("Error stopping Slack socket handler")

    # ------------------------------------------------------------------
    # Bolt event handler
    # ------------------------------------------------------------------

    async def _handle_mention(self, event: dict, say: Any) -> None:
        """Respond to @Alpha mentions in any channel."""
        raw_text: str = event.get("text", "")
        # Strip the bot mention token (<@BOTID>) from the message
        question = re.sub(r"<@\w+>", "", raw_text).strip()
        if not question:
            await say(
                text="Ask me something, e.g. `@alpha what did we decide about the launch?`",
                thread_ts=event.get("ts"),
            )
            return

        user_id = event.get("user", "")
        who = await self.user_name(user_id)
        channel = event.get("channel", "slack")
        logger.info("Mention from %s in #%s: %s", who, channel, question[:80])

        try:
            answer = await self._groq_service.ask_bot(question)
        except Exception as exc:
            logger.exception("Error handling mention")
            answer = f"Sorry, something went wrong: {exc}"

        await say(text=answer, thread_ts=event.get("ts"))
        self._file_service.save_conversation(f"#{channel}", who, question, answer)

    async def _handle_message(self, event: dict, say: Any = None) -> None:
        """Handle raw Slack message events (real-time chat capture and direct messages)."""
        # Skip bot messages, edits, joins, subtype events
        if event.get("bot_id") or event.get("subtype"):
            return

        text = (event.get("text") or "").strip()
        if not text:
            return

        user_id = event.get("user", "")
        who = await self.user_name(user_id) if user_id else "unknown"
        channel_id = event.get("channel", "general")
        channel_type = event.get("channel_type")
        is_dm = channel_type == "im" or str(channel_id).startswith("D")

        # Skip messages that mention the bot in public/private channels (handled by app_mention)
        # Note: In Slack, app_mention only fires in channels, never in 1-on-1 DMs!
        if not is_dm and self._bot_user_id and f"<@{self._bot_user_id}>" in text:
            return

        # Try to resolve channel name from cache if available
        channel_name = channel_id
        for ch in self._channel_cache:
            if ch.get("id") == channel_id:
                channel_name = ch.get("name", channel_id)
                break

        if is_dm and channel_name == channel_id:
            channel_name = f"dm-{who}"

        clean_msg = await self.clean_text(text)
        dt = datetime.now(tz=timezone.utc)
        self._file_service.append_slack_line(dt, channel_name, who, clean_msg)
        logger.debug("Captured message from %s in %s", who, channel_name)

        # If this is a direct message to the bot, answer the user directly in the DM!
        if is_dm and say is not None:
            question = re.sub(r"<@\w+>", "", text).strip()
            if question:
                try:
                    answer = await self._groq_service.ask_bot(question)
                except Exception as exc:
                    logger.exception("Error answering direct message")
                    answer = f"Sorry, something went wrong: {exc}"

                await say(text=answer, thread_ts=event.get("thread_ts"))
                self._file_service.save_conversation(f"DM:{who}", who, question, answer)

    # ------------------------------------------------------------------
    # User resolution
    # ------------------------------------------------------------------

    async def user_name(self, user_id: str) -> str:
        """Return display name for *user_id*, cached in memory."""
        if user_id in self._user_cache:
            return self._user_cache[user_id]
        try:
            resp = await self._web_client.users_info(user=user_id)
            profile = resp["user"].get("profile", {})
            name = (
                profile.get("display_name")
                or profile.get("real_name")
                or resp["user"].get("name")
                or user_id
            )
        except Exception:
            logger.warning("Could not resolve user %s", user_id)
            name = user_id
        self._user_cache[user_id] = name
        return name

    # ------------------------------------------------------------------
    # Text cleaning
    # ------------------------------------------------------------------

    async def clean_text(self, text: str) -> str:
        """Replace Slack user/channel mentions with human-readable names."""
        # Replace <@USERID> mentions
        for match in re.finditer(r"<@(\w+)>", text):
            uid = match.group(1)
            name = await self.user_name(uid)
            text = text.replace(match.group(0), f"@{name}")
        # Remove channel links like <#CHANID|channame>
        text = re.sub(r"<#\w+\|([^>]+)>", r"#\1", text)
        # Remove plain URLs wrapped in angle brackets
        text = re.sub(r"<(https?://[^|>]+)(?:\|[^>]*)?>", r"\1", text)
        return text.strip()

    # ------------------------------------------------------------------
    # Channel listing (cached 10 min)
    # ------------------------------------------------------------------

    async def list_channels(self, force: bool = False) -> list[dict]:
        """Return channels and direct messages the bot has access to (cached for 10 min)."""
        now = asyncio.get_event_loop().time()
        if not force and self._channel_cache and (now - self._channel_cache_ts) < _CHANNEL_CACHE_SECONDS:
            return self._channel_cache

        channels: list[dict] = []
        # 1. Fetch public & private channels
        # 2. Fetch direct messages (im) and group direct messages (mpim)
        for types_group in ("public_channel,private_channel", "im,mpim"):
            cursor: str | None = None
            while True:
                kwargs: dict[str, Any] = {
                    "types": types_group,
                    "exclude_archived": True,
                    "limit": 200,
                }
                if cursor:
                    kwargs["cursor"] = cursor
                try:
                    resp = await self._web_client.conversations_list(**kwargs)
                except Exception as exc:
                    logger.warning("Could not list conversations of type [%s]: %s", types_group, exc)
                    break

                for ch in resp.get("channels", []):
                    # Channels/groups check is_member; im/mpim bot is always participant or is_member
                    if ch.get("is_member") or ch.get("is_im") or ch.get("is_mpim"):
                        if ch.get("is_im"):
                            other_user_id = ch.get("user")
                            if other_user_id:
                                name = await self.user_name(other_user_id)
                                ch["name"] = f"dm-{name}"
                            else:
                                ch["name"] = f"dm-{ch['id']}"
                        channels.append(ch)

                cursor = (resp.get("response_metadata") or {}).get("next_cursor")
                if not cursor:
                    break

        self._channel_cache = channels
        self._channel_cache_ts = now
        logger.info("Listed %d joined channels and conversations", len(channels))
        return channels

    # ------------------------------------------------------------------
    # Channel sync
    # ------------------------------------------------------------------

    async def sync_channel(self, ch: dict, state: dict) -> int:
        """Fetch new messages for *ch* since the last known timestamp.

        Returns the number of new messages written.
        """
        channel_id: str = ch["id"]
        channel_name: str = ch.get("name") or channel_id
        if ch.get("is_im") and channel_name == channel_id:
            other_user_id = ch.get("user")
            if other_user_id:
                user_name = await self.user_name(other_user_id)
                channel_name = f"dm-{user_name}"

        oldest: str | None = state.get(channel_id)

        if oldest is None:
            # First sync: go back slack_sync_days days
            cutoff = datetime.now(tz=timezone.utc) - timedelta(days=self._settings.slack_sync_days)
            oldest = str(cutoff.timestamp())

        messages: list[dict] = []
        cursor: str | None = None
        while True:
            kwargs: dict[str, Any] = {
                "channel": channel_id,
                "oldest": oldest,
                "limit": 200,
            }
            if cursor:
                kwargs["cursor"] = cursor
            try:
                resp = await self._web_client.conversations_history(**kwargs)
            except Exception as exc:
                logger.warning("Error fetching history for %s: %s", channel_name, exc)
                return 0
            messages.extend(resp.get("messages", []))
            cursor = (resp.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                break

        # Sort ascending by timestamp
        messages.sort(key=lambda m: float(m.get("ts", 0)))

        count = 0
        latest_ts: str | None = None
        for msg in messages:
            msg_ts = msg.get("ts", "")
            if msg_ts == oldest:
                continue  # skip the boundary message already stored
            user_id = msg.get("user", "")
            text = msg.get("text", "")
            if not text:
                continue
            who = await self.user_name(user_id) if user_id else "app"
            text = await self.clean_text(text)
            dt = datetime.fromtimestamp(float(msg_ts), tz=timezone.utc)
            self._file_service.append_slack_line(dt, channel_name, who, text)
            latest_ts = msg_ts
            count += 1

        if latest_ts:
            state[channel_id] = latest_ts

        return count

    async def do_sync(self) -> None:
        """Sync all joined channels; updates and persists state."""
        state = self._file_service.load_state()
        channels = await self.list_channels()
        total = 0
        for ch in channels:
            n = await self.sync_channel(ch, state)
            total += n
        self._file_service.save_state(state)
        if total:
            logger.info("Slack sync complete: %d new messages across %d channels", total, len(channels))

    async def sync_slack(self) -> None:
        """Run do_sync() with a one-at-a-time guard."""
        if self._sync_lock.locked():
            logger.debug("Slack sync already in progress, skipping")
            return
        async with self._sync_lock:
            try:
                await self.do_sync()
            except Exception:
                logger.exception("Slack sync failed")

    # ------------------------------------------------------------------
    # Member sync
    # ------------------------------------------------------------------

    async def list_users(self) -> list[dict]:
        """Return all non-bot, non-deleted workspace members."""
        users: list[dict] = []
        cursor: str | None = None
        while True:
            kwargs: dict[str, Any] = {"limit": 200}
            if cursor:
                kwargs["cursor"] = cursor
            resp = await self._web_client.users_list(**kwargs)
            for u in resp.get("members", []):
                if not u.get("deleted") and not u.get("is_bot") and u.get("id") != "USLACKBOT":
                    users.append(u)
            cursor = (resp.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                break
        return users

    async def list_channel_member_ids(self, channel_id: str) -> list[str]:
        """Return user IDs of members of *channel_id*."""
        ids: list[str] = []
        cursor: str | None = None
        while True:
            kwargs: dict[str, Any] = {"channel": channel_id, "limit": 200}
            if cursor:
                kwargs["cursor"] = cursor
            try:
                resp = await self._web_client.conversations_members(**kwargs)
            except Exception:
                logger.warning("Could not list members of %s", channel_id)
                break
            ids.extend(resp.get("members", []))
            cursor = (resp.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                break
        return ids

    async def do_sync_members(self) -> None:
        """Write a fresh member list to slack_members.txt."""
        users = await self.list_users()
        lines: list[str] = []
        for u in users:
            profile = u.get("profile", {})
            display = profile.get("display_name") or profile.get("real_name") or u.get("name", "")
            real = profile.get("real_name", "")
            title = profile.get("title", "")
            tz = u.get("tz", "")
            line = f"{display} ({real})"
            if title:
                line += f" — {title}"
            if tz:
                line += f" [{tz}]"
            lines.append(line)
        content = "\n".join(sorted(lines))
        try:
            self._file_service.members_file.write_text(content, encoding="utf-8")
            logger.info("Member list updated: %d members", len(lines))
        except OSError:
            logger.exception("Failed to write members file")

    async def sync_members(self) -> None:
        """Run do_sync_members() with a one-at-a-time guard."""
        if self._members_lock.locked():
            logger.debug("Member sync already in progress, skipping")
            return
        async with self._members_lock:
            try:
                await self.do_sync_members()
            except Exception:
                logger.exception("Member sync failed")
