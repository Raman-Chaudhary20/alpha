"""Groq AI service — builds prompts from stored context and calls the API."""

from __future__ import annotations

import logging
import re

import groq as groq_sdk

from app.config import Settings
from app.services.file_service import FileService

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Text cleaning (port of the JS clean_reply helper)
# ---------------------------------------------------------------------------

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"\*(.+?)\*")
_HEADING_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_BACKTICK_RE = re.compile(r"`+")


def clean_reply(text: str) -> str:
    """Strip markdown formatting from an AI reply for plain-text delivery."""
    text = re.sub(r"^\s*\*\s+", "- ", text, flags=re.MULTILINE)       # "* item" -> "- item"
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text, flags=re.DOTALL)     # **bold**
    text = re.sub(r"__(.+?)__", r"\1", text, flags=re.DOTALL)         # __bold__
    text = re.sub(r"(?<!\w)\*(\S(?:.*?\S)?)\*(?!\w)", r"\1", text)   # *italic*
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)        # # headings
    text = re.sub(r"`{1,3}", "", text)                                # backticks
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Service class
# ---------------------------------------------------------------------------


class GroqService:
    """Wraps the async Groq client and owns the prompt-building logic."""

    _SYSTEM_TEMPLATE = """\
Your name is Alpha. You are a helpful assistant inside a Slack workspace. You have three knowledge sources: <slack_messages> (messages from the workspace channels), <slack_members> (who is in the workspace and in each channel) and <conversation_history> (your earlier conversations with users). Use all of them to answer.

Reply rules:
- Plain text only. No markdown: no ** or * for emphasis, no # headings, no tables, no backticks.
- Be short and direct: 1 to 3 sentences unless the user asks for detail.
- Answer only what was asked. Do not recap the whole project.
- Use lines starting with '- ' only when listing several items.
- Never mention file names or the sources you used.
- If the answer is not in the sources, say you don't know.

<slack_messages>
{slack_messages}
</slack_messages>

<slack_members>
{slack_members}
</slack_members>

<conversation_history>
{conversation_history}
</conversation_history>"""

    def __init__(self, settings: Settings, file_service: FileService) -> None:
        self._settings = settings
        self._file_service = file_service
        self._client = groq_sdk.AsyncGroq(api_key=settings.groq_api_key)

    def _build_system_prompt(self) -> str:
        fs = self._file_service
        s = self._settings

        slack_messages = fs.tail(fs.slack_file, s.max_slack_chars) or "(none yet)"
        slack_members = fs.head(fs.members_file, s.max_members_chars) or "(none yet)"
        conversation_history = (
            fs.tail(fs.conversation_file, s.max_conversation_chars)
            or "(none yet)"
        )

        return self._SYSTEM_TEMPLATE.format(
            slack_messages=slack_messages,
            slack_members=slack_members,
            conversation_history=conversation_history,
        )

    async def ask_bot(self, question: str) -> str:
        """Send *question* to Groq and return a cleaned plain-text answer."""
        system_prompt = self._build_system_prompt()
        try:
            response = await self._client.chat.completions.create(
                model=self._settings.groq_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": question},
                ],
                temperature=0.3,
                max_tokens=1024,
            )
            raw = response.choices[0].message.content or ""
            return clean_reply(raw)
        except groq_sdk.APIError as exc:
            logger.error("Groq API error: %s", exc)
            raise
