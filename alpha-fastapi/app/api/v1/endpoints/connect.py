"""Each person's one-time Slack sign-in, so Alpha can read their DMs and private chats.

Slack never lets a bot read a DM it is not part of. On Slack's Free and Pro plans the only
way is for each person to allow it: they open /api/v1/slack/connect, click Allow on
Slack's own page, and Slack sends them back here with a code that becomes their user
token. The token is stored encrypted and is never returned by any endpoint.
"""

from __future__ import annotations

import asyncio
import html
import logging
from collections.abc import Awaitable, Callable
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from app.config import Settings
from app.core.secrets import Secrets
from app.dependencies import get_secrets, get_settings_dep, get_slack_service, get_store, require_admin
from app.services.harvest_service import USER_SCOPES, error_code
from app.services.slack_service import SlackService
from app.services.store import Store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/slack")

Exchange = Callable[[Settings, str], Awaitable[dict]]


async def slack_exchange(settings: Settings, code: str) -> dict:
    resp = await AsyncWebClient().oauth_v2_access(
        client_id=settings.slack_client_id,
        client_secret=settings.slack_client_secret,
        code=code,
        redirect_uri=settings.redirect_uri,
    )
    return dict(resp.data)


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(
        status_code=status,
        content=(
            "<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
            f"<title>{html.escape(title)}</title></head>"
            "<body style='font-family:system-ui,sans-serif;max-width:560px;margin:64px auto;padding:0 16px;line-height:1.5'>"
            f"<h1 style='font-size:22px'>{html.escape(title)}</h1><p>{html.escape(body)}</p></body></html>"
        ),
    )


def _ready(settings: Settings, secrets: Secrets) -> None:
    missing = [
        name
        for name, value in (
            ("SLACK_CLIENT_ID", settings.slack_client_id),
            ("SLACK_CLIENT_SECRET", settings.slack_client_secret),
            ("PUBLIC_URL", settings.public_url),
            ("TOKEN_KEY", secrets.ready),
        )
        if not value
    ]
    if missing:
        raise HTTPException(status_code=503, detail="Sign-in is not set up: missing " + ", ".join(missing))


@router.get("/connect", summary="Send a person to Slack to let Alpha read their conversations")
async def connect(
    settings: Settings = Depends(get_settings_dep),
    secrets: Secrets = Depends(get_secrets),
) -> RedirectResponse:
    _ready(settings, secrets)
    query = urlencode(
        {
            "client_id": settings.slack_client_id,
            "user_scope": USER_SCOPES,
            "redirect_uri": settings.redirect_uri,
            "state": secrets.make_state(),
        }
    )
    return RedirectResponse("https://slack.com/oauth/v2/authorize?" + query, status_code=302)


@router.get("/oauth/callback", summary="Where Slack sends a person back after they click Allow")
async def oauth_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    settings: Settings = Depends(get_settings_dep),
    secrets: Secrets = Depends(get_secrets),
    store: Store = Depends(get_store),
    slack_service: SlackService | None = Depends(get_slack_service),
) -> HTMLResponse:
    _ready(settings, secrets)
    if error:
        return _page("Not connected", "You did not allow Alpha to read your conversations. Nothing was changed.")
    if not code or not secrets.check_state(state):
        return _page("Link expired", "This sign-in link is invalid or older than 15 minutes. Ask Alpha for a new one.", 400)

    exchange: Exchange = getattr(request.app.state, "oauth_exchange", None) or slack_exchange
    try:
        data = await exchange(settings, code)
    except SlackApiError as exc:
        logger.warning("Slack sign-in exchange failed: %s", error_code(exc))
        return _page("Not connected", "Slack refused the sign-in. Please try again from a new link.", 400)

    user = data.get("authed_user") or {}
    token = user.get("access_token")
    team_id = (data.get("team") or {}).get("id")
    expected_team = store.get_meta("team_id")
    if not data.get("ok", True) or not token or not user.get("id"):
        return _page("Not connected", "Slack did not return a sign-in for you. Please try again.", 400)
    if expected_team and team_id != expected_team:
        return _page("Wrong workspace", "That sign-in belongs to a different Slack workspace than Alpha's.", 400)

    store.save_token(user["id"], secrets.encrypt(token), user.get("scope", ""), team_id)
    logger.info("Slack sign-in saved for %s", user["id"])
    if slack_service is not None:
        asyncio.create_task(slack_service.sync_slack())
    return _page(
        "Connected",
        "Alpha can now read your Slack conversations, including your DMs, and will start in a minute. "
        "Send Alpha the word disconnect in a direct message to stop.",
    )


@router.get("/connections", summary="Who has let Alpha read their conversations", dependencies=[Depends(require_admin)])
async def connections(store: Store = Depends(get_store)) -> dict:
    tokens = {t["user_id"]: t for t in store.all_tokens()}
    people = []
    for user in store.users():
        token = tokens.get(user["id"])
        people.append(
            {
                "id": user["id"],
                "name": user["name"],
                "connected": bool(token and not token["failed_at"]),
                "sign_in_stopped_working": bool(token and token["failed_at"]),
            }
        )
    return {
        "connected": sum(1 for p in people if p["connected"]),
        "people": len(people),
        "list": people,
    }


@router.post("/invite", summary="DM everyone not yet connected with the sign-in link", dependencies=[Depends(require_admin)])
async def invite(slack_service: SlackService | None = Depends(get_slack_service)) -> dict:
    if slack_service is None:
        raise HTTPException(status_code=400, detail="Slack is not configured on this instance.")
    return await slack_service.invite()
