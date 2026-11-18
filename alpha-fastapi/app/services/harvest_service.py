"""Reads every conversation Alpha can reach and stores each message once.

Readers:
- the bot token: channels the bot is in, and DMs sent to the bot;
- every person who signed in (Slack user token): all their channels, private channels,
  DMs and group DMs.

A conversation reachable by several readers (a DM read through both people, a channel
many people are in) is listed once and read through the first reader that can. Each
message is stored under (conversation, ts), so reading it again never duplicates it.
Thread replies and shared files (with their text, where it can be extracted) are read too.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from app.config import Settings
from app.core.secrets import Secrets
from app.services.store import Store

logger = logging.getLogger(__name__)

# Conversation types Alpha reads.
ALL_TYPES = "public_channel,private_channel,mpim,im"
# Events that carry no content.
SKIP_SUBTYPES = {"channel_join", "channel_leave", "group_join", "group_leave", "message_deleted"}
# The reader cannot see this conversation; another reader may.
NOT_FOR_THIS_READER = {"not_in_channel", "channel_not_found", "missing_scope", "not_allowed_token_type"}
# The person's sign-in no longer works.
SIGN_IN_GONE = {"token_revoked", "invalid_auth", "account_inactive", "token_expired", "not_authed"}

# Scopes Alpha asks each person for when they sign in.
USER_SCOPES = (
    "channels:history,channels:read,groups:history,groups:read,"
    "im:history,im:read,mpim:history,mpim:read,files:read,users:read"
)


def error_code(exc: SlackApiError) -> str:
    response = exc.response
    try:
        return str(response.get("error") or "")
    except AttributeError:
        return ""


@dataclass
class Reader:
    label: str                    # "bot" or the person's user id
    client: Any                   # AsyncWebClient or a stand-in with the same methods
    owner: str | None = None      # the person's user id, None for the bot
    token: str = field(default="", repr=False)


@dataclass
class HarvestResult:
    conversations: int = 0
    new_messages: int = 0
    files: int = 0
    readers: int = 0
    failed_sign_ins: list[str] = field(default_factory=list)


def make_client(token: str) -> AsyncWebClient:
    from slack_sdk.http_retry.builtin_async_handlers import AsyncRateLimitErrorRetryHandler

    client = AsyncWebClient(token=token, timeout=30)
    client.retry_handlers.append(AsyncRateLimitErrorRetryHandler(max_retry_count=5))
    return client


async def download(url: str, token: str, max_bytes: int) -> bytes | None:
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as http:
        async with http.stream("GET", url, headers={"Authorization": f"Bearer {token}"}) as resp:
            if resp.status_code != 200:
                return None
            if "text/html" in resp.headers.get("content-type", ""):
                return None  # Slack answers a sign-in page when the token cannot see the file
            chunks = []
            size = 0
            async for chunk in resp.aiter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    return None
                chunks.append(chunk)
            return b"".join(chunks)


def extract_text(name: str, mimetype: str, data: bytes, max_chars: int) -> str | None:
    """Text of a shared file, for the formats people share most. None when not readable."""
    lower = name.lower()
    try:
        if mimetype.startswith("text/") or mimetype in ("application/json", "application/xml") or lower.endswith(
            (".txt", ".md", ".csv", ".json", ".log", ".yaml", ".yml", ".xml", ".py", ".js", ".ts", ".sql")
        ):
            return data.decode("utf-8", errors="replace")[:max_chars]
        if mimetype == "application/pdf" or lower.endswith(".pdf"):
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data))
            parts = []
            total = 0
            for page in reader.pages:
                part = page.extract_text() or ""
                parts.append(part)
                total += len(part)
                if total > max_chars:
                    break
            return "\n".join(parts)[:max_chars]
        if lower.endswith(".docx"):
            import docx

            document = docx.Document(io.BytesIO(data))
            return "\n".join(p.text for p in document.paragraphs)[:max_chars]
        if lower.endswith(".xlsx"):
            from openpyxl import load_workbook

            book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            lines = []
            total = 0
            for sheet in book.worksheets:
                lines.append(f"Sheet {sheet.title}")
                for row in sheet.iter_rows(values_only=True):
                    line = " | ".join("" if v is None else str(v) for v in row).strip(" |")
                    if line:
                        lines.append(line)
                        total += len(line)
                    if total > max_chars:
                        break
            return "\n".join(lines)[:max_chars]
    except Exception:  # a broken file must not stop the sync
        logger.warning("Could not read the text of file %s", name)
    return None


class Harvester:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        secrets: Secrets,
        bot_client: Any | None,
        client_factory: Callable[[str], Any] = make_client,
        fetch: Callable[[str, str, int], Awaitable[bytes | None]] = download,
    ) -> None:
        self._settings = settings
        self._store = store
        self._secrets = secrets
        self._bot = bot_client
        self._client_factory = client_factory
        self._fetch = fetch
        self._lock = asyncio.Lock()
        self.last_result: HarvestResult | None = None

    # ------------------------------------------------------------------ readers
    def readers(self) -> list[Reader]:
        out: list[Reader] = []
        if self._bot is not None:
            out.append(Reader("bot", self._bot, None, getattr(self._bot, "token", "") or ""))
        for row in self._store.tokens():
            token = self._secrets.decrypt(row["token_enc"]) if self._secrets.ready else None
            if not token:
                logger.warning("The sign-in of %s cannot be read (TOKEN_KEY changed?)", row["user_id"])
                continue
            out.append(Reader(row["user_id"], self._client_factory(token), row["user_id"], token))
        return out

    # ------------------------------------------------------------------ people
    async def sync_users(self) -> int:
        readers = self.readers()
        for reader in readers:
            try:
                count = 0
                cursor = None
                while True:
                    kwargs: dict[str, Any] = {"limit": 200}
                    if cursor:
                        kwargs["cursor"] = cursor
                    resp = await reader.client.users_list(**kwargs)
                    for user in resp.get("members", []):
                        self._store.upsert_user(user)
                        count += 1
                    cursor = (resp.get("response_metadata") or {}).get("next_cursor")
                    if not cursor:
                        break
                return count
            except SlackApiError as exc:
                logger.warning("Could not list people through %s: %s", reader.label, error_code(exc))
        return 0

    async def _name(self, client: Any, user_id: str | None) -> str:
        if not user_id:
            return "app"
        name = self._store.user_name(user_id)
        if name:
            return name
        try:
            resp = await client.users_info(user=user_id)
            self._store.upsert_user(resp["user"])
            return self._store.user_name(user_id) or user_id
        except (SlackApiError, KeyError, TypeError):
            return user_id

    async def clean(self, client: Any, text: str) -> str:
        for match in set(re.findall(r"<@(\w+)(?:\|[^>]*)?>", text)):
            name = await self._name(client, match)
            text = re.sub(rf"<@{match}(?:\|[^>]*)?>", f"@{name}", text)
        text = re.sub(r"<#\w+\|([^>]*)>", r"#\1", text)
        text = re.sub(r"<!(here|channel|everyone)[^>]*>", r"@\1", text)
        text = re.sub(r"<(https?://[^|>]+)\|([^>]+)>", r"\2 (\1)", text)
        text = re.sub(r"<(https?://[^|>]+)>", r"\1", text)
        return text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").strip()

    # ------------------------------------------------------------------ listing
    async def _list(self, reader: Reader) -> list[dict]:
        """Every conversation this reader is in. Asked one type at a time, so a type the
        app's scopes do not allow (e.g. group DMs without mpim:read) is skipped, not fatal."""
        conversations: list[dict] = []
        bot_user = self._bot_user_id()
        for kind in ALL_TYPES.split(","):
            cursor = None
            while True:
                kwargs: dict[str, Any] = {"types": kind, "limit": 200, "exclude_archived": False}
                if cursor:
                    kwargs["cursor"] = cursor
                try:
                    if reader.owner:
                        resp = await reader.client.users_conversations(**kwargs)
                    else:
                        resp = await reader.client.conversations_list(**kwargs)
                except SlackApiError as exc:
                    if error_code(exc) == "missing_scope":
                        logger.warning("%s cannot list %s: the app lacks the scope", reader.label, kind)
                        break
                    raise
                for ch in resp.get("channels", []):
                    # DMs with Alpha itself are questions to the bot, not the organization's chat.
                    if ch.get("is_im") and (not reader.owner or (bot_user and ch.get("user") == bot_user)):
                        continue
                    if reader.owner or ch.get("is_member") or ch.get("is_mpim"):
                        conversations.append(ch)
                cursor = (resp.get("response_metadata") or {}).get("next_cursor")
                if not cursor:
                    break
        return conversations

    async def _describe(self, reader: Reader, ch: dict) -> tuple[str, str, list[str]]:
        """(kind, name, members) of a conversation."""
        if ch.get("is_im"):
            me = reader.owner or self._bot_user_id()
            members = [m for m in (me, ch.get("user")) if m]
            names = sorted([await self._name(reader.client, m) for m in members])
            return "im", "DM: " + " & ".join(names), members
        if ch.get("is_mpim"):
            members: list[str] = []
            try:
                resp = await reader.client.conversations_members(channel=ch["id"], limit=200)
                members = list(resp.get("members", []))
            except SlackApiError:
                pass
            names = sorted([await self._name(reader.client, m) for m in members])
            return "mpim", "Group DM: " + (", ".join(names) or ch.get("name", ch["id"])), members
        kind = "private" if (ch.get("is_private") or ch.get("is_group")) else "public"
        return kind, "#" + (ch.get("name") or ch["id"]), []

    def _bot_user_id(self) -> str | None:
        return self._store.get_meta("bot_user_id")

    # ------------------------------------------------------------------ harvest
    async def harvest(self) -> HarvestResult:
        """Read everything new. One harvest runs at a time; a second call waits for it."""
        async with self._lock:
            result = HarvestResult()
            readers = self.readers()
            result.readers = len(readers)
            reach: dict[str, list[Reader]] = {}
            info: dict[str, tuple[str, str, list[str]]] = {}
            for reader in readers:
                try:
                    listed = await self._list(reader)
                except SlackApiError as exc:
                    code = error_code(exc)
                    logger.warning("Could not list conversations through %s: %s", reader.label, code)
                    if reader.owner and code in SIGN_IN_GONE:
                        self._store.mark_token_failed(reader.owner)
                        result.failed_sign_ins.append(reader.owner)
                    continue
                for ch in listed:
                    reach.setdefault(ch["id"], []).append(reader)
                    if ch["id"] not in info or ch.get("is_im") and reader.owner:
                        info[ch["id"]] = await self._describe(reader, ch)

            for conversation_id, conv_readers in reach.items():
                kind, name, members = info[conversation_id]
                self._store.upsert_conversation(conversation_id, kind, name, members)
                new, files = await self._sync_conversation(conversation_id, conv_readers, result)
                result.conversations += 1
                result.new_messages += new
                result.files += files
            self.last_result = result
            if result.new_messages:
                logger.info(
                    "Harvest: %d new or changed messages, %d files, %d conversations, %d readers",
                    result.new_messages, result.files, result.conversations, result.readers,
                )
            return result

    async def _sync_conversation(
        self, conversation_id: str, readers: list[Reader], result: HarvestResult
    ) -> tuple[int, int]:
        now = time.time()
        last = self._store.last_ts(conversation_id)
        if last is None:
            oldest = now - self._settings.slack_sync_days * 86400
        else:
            oldest = min(float(last), now - self._settings.slack_reread_days * 86400)
        for reader in readers:
            if reader.owner and reader.owner in result.failed_sign_ins:
                continue
            try:
                return await self._read(conversation_id, reader, f"{oldest:.6f}")
            except SlackApiError as exc:
                code = error_code(exc)
                if reader.owner and code in SIGN_IN_GONE:
                    self._store.mark_token_failed(reader.owner)
                    result.failed_sign_ins.append(reader.owner)
                elif code not in NOT_FOR_THIS_READER:
                    logger.warning("Reading %s through %s failed: %s", conversation_id, reader.label, code)
        return 0, 0

    async def _read(self, conversation_id: str, reader: Reader, oldest: str) -> tuple[int, int]:
        messages: list[dict] = []
        cursor = None
        while True:
            kwargs: dict[str, Any] = {"channel": conversation_id, "oldest": oldest, "limit": 200}
            if cursor:
                kwargs["cursor"] = cursor
            resp = await reader.client.conversations_history(**kwargs)
            messages.extend(resp.get("messages", []))
            cursor = (resp.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                break

        threads = [m for m in messages if int(m.get("reply_count") or 0) > 0]
        for parent in threads:
            cursor = None
            while True:
                kwargs = {"channel": conversation_id, "ts": parent["ts"], "limit": 200}
                if cursor:
                    kwargs["cursor"] = cursor
                try:
                    resp = await reader.client.conversations_replies(**kwargs)
                except SlackApiError as exc:
                    logger.warning("Thread %s in %s: %s", parent["ts"], conversation_id, error_code(exc))
                    break
                messages.extend(m for m in resp.get("messages", []) if m.get("ts") != parent["ts"])
                cursor = (resp.get("response_metadata") or {}).get("next_cursor")
                if not cursor:
                    break

        new = 0
        files = 0
        latest = None
        for msg in sorted(messages, key=lambda m: float(m.get("ts", 0))):
            if msg.get("subtype") in SKIP_SUBTYPES:
                continue
            ts = msg.get("ts")
            if not ts:
                continue
            text = msg.get("text") or ""
            if not text:
                text = "\n".join(
                    a.get("fallback") or a.get("text") or "" for a in msg.get("attachments") or []
                ).strip()
            text = await self.clean(reader.client, text)
            user_id = msg.get("user") or msg.get("bot_id")
            file_ids = []
            for item in msg.get("files") or []:
                if not item.get("id"):
                    continue
                file_ids.append(item["id"])
                if await self._take_file(reader, conversation_id, ts, user_id, item):
                    files += 1
            if not text and not file_ids:
                continue
            if self._store.upsert_message(
                conversation_id,
                ts,
                user_id,
                text,
                thread_ts=msg.get("thread_ts"),
                reply_count=int(msg.get("reply_count") or 0),
                files=file_ids,
            ):
                new += 1
            if msg.get("thread_ts") in (None, ts):
                latest = ts if latest is None or float(ts) > float(latest) else latest
        if latest:
            self._store.set_last_ts(conversation_id, latest)
        return new, files

    async def _take_file(
        self, reader: Reader, conversation_id: str, ts: str, user_id: str | None, item: dict
    ) -> bool:
        if self._store.has_file(item["id"]):
            return False
        name = item.get("name") or item.get("title") or item["id"]
        mimetype = item.get("mimetype") or ""
        size = int(item.get("size") or 0)
        text = None
        url = item.get("url_private_download") or item.get("url_private")
        if (
            url
            and reader.token
            and item.get("mode") not in ("hidden_by_limit", "tombstone", "external")
            and size <= self._settings.max_file_bytes
        ):
            try:
                data = await self._fetch(url, reader.token, self._settings.max_file_bytes)
            except httpx.HTTPError:
                logger.warning("Could not download file %s", name)
                return False  # try again next sync
            if data:
                text = extract_text(name, mimetype, data, self._settings.max_file_chars)
        self._store.save_file(item["id"], conversation_id, ts, user_id, name, mimetype, size, text)
        return True
