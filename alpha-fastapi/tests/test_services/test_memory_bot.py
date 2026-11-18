"""Alpha as the organization's memory: reading everything once, structuring it, answering by scope."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from slack_sdk.errors import SlackApiError

from app.config import Settings
from app.core.secrets import Secrets
from app.main import create_app
from app.services.bedrock_service import Brain
from app.services.harvest_service import Harvester
from app.services.memory_service import MemoryBuilder, keep_unmentioned
from app.services.slack_service import SlackService
from app.services.store import Store

NOW = time.time()


def ts(minutes_ago: float) -> str:
    return f"{NOW - minutes_ago * 60:.6f}"


# ---------------------------------------------------------------------------
# A stand-in Slack: one workspace, three people, and who can see what
# ---------------------------------------------------------------------------

USERS = [
    {"id": "U1", "name": "asha", "profile": {"display_name": "Asha", "real_name": "Asha Rao", "title": "PM"}, "is_admin": True},
    {"id": "U2", "name": "ravi", "profile": {"display_name": "Ravi", "real_name": "Ravi K", "title": "Engineer"}},
    {"id": "U3", "name": "meera", "profile": {"display_name": "Meera", "real_name": "Meera S"}},
    {"id": "UBOT", "name": "alpha", "is_bot": True, "profile": {"display_name": "alpha"}},
]

DM_MESSAGES = [
    {"ts": ts(30), "user": "U1", "text": "Ravi, can you take the billing revamp? Client wants it by Friday"},
    {"ts": ts(29), "user": "U2", "text": "Yes, I'll lead billing revamp. <@U1> please send the spec", "reply_count": 1,
     "files": [{"id": "F1", "name": "spec.txt", "mimetype": "text/plain", "size": 40, "url_private_download": "https://files/F1"}]},
]
THREAD = [
    {"ts": DM_MESSAGES[1]["ts"], "user": "U2", "text": DM_MESSAGES[1]["text"]},
    {"ts": ts(28), "thread_ts": DM_MESSAGES[1]["ts"], "user": "U1", "text": "Sent. Budget is 12 lakh"},
]
PRIVATE = [{"ts": ts(20), "user": "U1", "text": "Hiring plan for Q1 is confidential"}]
PUBLIC = [
    {"ts": ts(10), "user": "U3", "text": "Website launch is live, thanks all"},
    {"ts": ts(9), "subtype": "channel_join", "user": "U2", "text": "<@U2> has joined the channel"},
]
BOT_DM = [{"ts": ts(5), "user": "U1", "text": "how many projects?"}]


def _of_type(channels: list[dict], types: str) -> list[dict]:
    def kind(ch):
        if ch.get("is_im"):
            return "im"
        if ch.get("is_mpim"):
            return "mpim"
        return "private_channel" if ch.get("is_private") else "public_channel"
    return [ch for ch in channels if kind(ch) in types.split(",")]


class FakeSlack:
    """The methods Alpha calls, answering as the given reader would see Slack."""

    def __init__(self, who: str, token: str = "", revoked: bool = False) -> None:
        self.who = who
        self.token = token
        self.revoked = revoked
        self.history_calls: list[str] = []

    def _check(self) -> None:
        if self.revoked:
            raise SlackApiError("revoked", {"ok": False, "error": "token_revoked"})

    async def users_list(self, **_):
        return {"members": USERS}

    async def users_info(self, user: str):
        return {"user": next(u for u in USERS if u["id"] == user)}

    async def conversations_list(self, types: str = "public_channel", **_):
        self._check()
        if "mpim" in types:
            raise SlackApiError("scope", {"ok": False, "error": "missing_scope"})  # like Alpha's bot today
        return {"channels": _of_type([
            {"id": "C1", "name": "general", "is_member": True},
            {"id": "C2", "name": "random", "is_member": False},
            {"id": "DBOT", "is_im": True, "user": "U1"},
        ], types)}

    async def users_conversations(self, types: str = "public_channel", **_):
        self._check()
        channels = [{"id": "C1", "name": "general"}, {"id": "DBOT2", "is_im": True, "user": "UBOT"}]
        if self.who in ("U1", "U2"):
            channels.append({"id": "D12", "is_im": True, "user": "U2" if self.who == "U1" else "U1"})
        if self.who == "U1":
            channels.append({"id": "G1", "name": "leadership", "is_private": True})
        return {"channels": _of_type(channels, types)}

    async def conversations_members(self, channel: str, **_):
        return {"members": []}

    async def conversations_history(self, channel: str, **_):
        self._check()
        self.history_calls.append(channel)
        data = {"C1": PUBLIC, "D12": DM_MESSAGES, "G1": PRIVATE, "DBOT": BOT_DM}
        if channel == "C1" and self.who != "bot":
            raise SlackApiError("no", {"ok": False, "error": "not_in_channel"})
        return {"messages": list(reversed(data[channel]))}

    async def conversations_replies(self, channel: str, ts: str, **_):
        return {"messages": THREAD}


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(data_dir=str(tmp_path / "data"), token_key="test-key-123", slack_client_id="cid",
                    slack_client_secret="secret", public_url="https://alpha.test", api_key="admin-key")


@pytest.fixture
def store(settings) -> Store:
    s = Store(f"{settings.data_dir}/alpha.db")
    s.set_meta("bot_user_id", "UBOT")
    return s


def connect_people(store: Store, secrets: Secrets, *who: str) -> None:
    for user_id in who:
        store.save_token(user_id, secrets.encrypt(f"xoxp-{user_id}"), "im:history", "T1")


def harvester(settings: Settings, store: Store, clients: dict) -> Harvester:
    secrets = Secrets(settings.token_key)

    def factory(token: str):
        user = token.split("-", 1)[1]
        clients.setdefault(user, FakeSlack(user, token))
        return clients[user]

    async def fetch(url, token, max_bytes):
        return b"Billing revamp spec: invoices v2"

    bot = FakeSlack("bot", "xoxb-bot")
    clients["bot"] = bot
    return Harvester(settings, store, secrets, bot, client_factory=factory, fetch=fetch)


# ---------------------------------------------------------------------------
# Reading every conversation once
# ---------------------------------------------------------------------------

async def test_a_dm_read_through_both_people_is_stored_once(settings, store):
    secrets = Secrets(settings.token_key)
    connect_people(store, secrets, "U1", "U2")
    clients: dict = {}
    h = harvester(settings, store, clients)
    await h.sync_users()

    first = await h.harvest()
    again = await h.harvest()

    rows = store._all("SELECT conversation_id, COUNT(*) AS n FROM messages GROUP BY conversation_id")
    counts = {r["conversation_id"]: r["n"] for r in rows}
    assert counts == {"C1": 1, "D12": 3, "G1": 1}  # 2 DM messages + 1 thread reply; the join event skipped
    assert first.new_messages == 5
    assert again.new_messages == 0  # rereading changes nothing
    # The DM is read through one reader only per harvest, not once per person.
    reads = sum(c.history_calls.count("D12") for c in clients.values())
    assert reads == 2  # one per harvest
    dm = store.conversation("D12")
    assert dm["kind"] == "im" and dm["name"] == "DM: Asha & Ravi"
    assert store.conversation("G1")["kind"] == "private"


async def test_dms_with_alpha_itself_are_not_memory(settings, store):
    secrets = Secrets(settings.token_key)
    connect_people(store, secrets, "U1")
    h = harvester(settings, store, {})
    await h.harvest()
    assert store.conversation("DBOT") is None
    assert store.conversation("DBOT2") is None


async def test_threads_files_and_mentions_are_read(settings, store):
    secrets = Secrets(settings.token_key)
    connect_people(store, secrets, "U1")
    h = harvester(settings, store, {})
    await h.sync_users()
    await h.harvest()
    texts = [r["text"] for r in store._all("SELECT text FROM messages WHERE conversation_id = 'D12' ORDER BY ts")]
    assert "Sent. Budget is 12 lakh" in texts
    assert any("@Asha please send the spec" in t for t in texts)
    assert store.file_texts(["F1"], 100) == [("spec.txt", "Billing revamp spec: invoices v2")]
    assert store.search("invoices", "all")[0]["type"] == "file"


async def test_a_revoked_sign_in_is_marked_and_others_still_read(settings, store):
    secrets = Secrets(settings.token_key)
    connect_people(store, secrets, "U1", "U2")
    clients = {"U1": FakeSlack("U1", "xoxp-U1", revoked=True)}
    h = harvester(settings, store, clients)
    result = await h.harvest()
    assert result.failed_sign_ins == ["U1"]
    assert [t["user_id"] for t in store.tokens()] == ["U2"]
    assert store.conversation("D12") is not None  # still read through Ravi


def test_an_edit_updates_the_message_without_a_copy(store):
    store.upsert_conversation("C9", "public", "#x", [])
    assert store.upsert_message("C9", "1.0", "U1", "first") is True
    assert store.upsert_message("C9", "1.0", "U1", "first") is False
    assert store.upsert_message("C9", "1.0", "U1", "edited") is True
    assert store.message_count() == 1
    assert store.search("edited", "all") and not store.search("first", "all")


def test_the_same_words_pasted_in_two_chats_come_back_once(store):
    store.upsert_conversation("C1", "public", "#general", [])
    store.upsert_conversation("C2", "public", "#eng", [])
    store.upsert_message("C1", "1.0", "U1", "Deploy freeze starts Monday")
    store.upsert_message("C2", "2.0", "U1", "Deploy freeze starts Monday!")
    results = store.search("deploy freeze", "all")
    assert len(results) == 1 and results[0]["also_in"] == ["#eng"]


# ---------------------------------------------------------------------------
# Who may see what
# ---------------------------------------------------------------------------

def test_public_scope_never_returns_private_chats(store):
    store.upsert_conversation("C1", "public", "#general", [])
    store.upsert_conversation("D1", "im", "DM: A & B", [])
    store.upsert_conversation("G1", "private", "#leaders", [])
    store.upsert_message("C1", "1.0", "U1", "budget for website")
    store.upsert_message("D1", "2.0", "U1", "budget for billing")
    store.upsert_message("G1", "3.0", "U1", "budget for hiring")
    assert {r["conversation"] for r in store.search("budget", "public")} == {"#general"}
    assert {r["conversation"] for r in store.search("budget", "all")} == {"#general", "DM: A & B", "#leaders"}
    with pytest.raises(ValueError):
        store.search("budget", "everything")


def test_registers_are_kept_per_scope(settings, store):
    brain = Brain(settings, store, MagicMock())
    store.replace_knowledge("all", "project", [{"name": "Billing revamp", "status": "active"}])
    store.replace_knowledge("public", "project", [{"name": "Website launch", "status": "done"}])
    assert brain.run_tool("list_projects", {}, "public")["projects"] == [{"name": "Website launch", "status": "done"}]
    assert brain.run_tool("list_projects", {}, "all")["count"] == 1
    assert brain.run_tool("get_project", {"name": "billing"}, "public")["found"] is False


def _slack_service(settings, store) -> SlackService:
    settings = settings.model_copy(update={"slack_bot_token": "xoxb-test"})
    return SlackService(settings, MagicMock(), store, MagicMock(), MagicMock(), Secrets(settings.token_key),
                        bot_client=MagicMock())


def test_only_an_admin_in_a_dm_gets_private_chats(settings, store):
    for user in USERS:
        store.upsert_user(user)
    service = _slack_service(settings, store)
    assert service.scope_for("U1", is_dm=True) == "all"       # Slack workspace admin
    assert service.scope_for("U1", is_dm=False) == "public"   # in a channel everyone reads the answer
    assert service.scope_for("U2", is_dm=True) == "public"
    listed = settings.model_copy(update={"admin_user_ids": "U2"})
    assert _slack_service(listed, store).scope_for("U2", is_dm=True) == "all"


class FakeBedrock:
    """Asks for one search, then answers with what the search returned."""

    def __init__(self) -> None:
        self.seen: list = []

    async def converse(self, **kwargs):
        last = kwargs["messages"][-1]["content"][0]
        if "toolResult" in last:
            self.seen.append(last["toolResult"]["content"][0]["json"]["result"])
            found = [r["text"] for r in self.seen[-1]["results"]]
            return {"output": {"message": {"role": "assistant", "content": [{"text": "**Found:** " + "; ".join(found)}]}}}
        return {"output": {"message": {"role": "assistant", "content": [
            {"toolUse": {"toolUseId": "t1", "name": "search_messages", "input": {"query": "budget"}}}]}}}


async def test_the_model_cannot_see_past_the_scope_it_is_given(settings, store):
    store.upsert_conversation("C1", "public", "#general", [])
    store.upsert_conversation("D1", "im", "DM: A & B", [])
    store.upsert_message("C1", "1.0", "U1", "budget for website")
    store.upsert_message("D1", "2.0", "U1", "budget for billing")
    bedrock = FakeBedrock()
    brain = Brain(settings, store, bedrock)
    public = await brain.ask_bot("what budgets?", scope="public")
    everything = await brain.ask_bot("what budgets?", scope="all")
    assert public == "Found: budget for website"
    assert "budget for billing" in everything


# ---------------------------------------------------------------------------
# Structuring: day summaries and the register
# ---------------------------------------------------------------------------

class FakeStructured:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def structured(self, model, system, prompt, tool_name, description, schema, max_tokens=4000):
        self.calls.append(tool_name)
        if tool_name == "record_day":
            name = "Billing revamp" if "billing" in prompt else "Website launch"
            return {"summary": f"Talked about {name}.", "projects": [{"name": name, "status": "active",
                    "people": [{"name": "Ravi", "role": "leads"}]}]}
        projects = {}
        for line in prompt.split("Talked about ")[1:]:
            name = line.split(".")[0]
            projects[name] = {"name": name, "status": "active", "summary": name, "people": [{"name": "Ravi", "role": "leads"}]}
        return {"projects": sorted(projects.values(), key=lambda p: p["name"]),
                "people": [{"name": "Ravi", "works_on": sorted(projects)}]}


async def test_memory_is_built_once_and_again_only_when_chats_change(settings, store):
    store.upsert_conversation("C1", "public", "#general", [])
    store.upsert_conversation("D1", "im", "DM: Asha & Ravi", [])
    store.upsert_message("C1", ts(10), "U3", "Website launch is live")
    store.upsert_message("D1", ts(9), "U2", "I'll lead the billing revamp")
    fake = FakeStructured()
    builder = MemoryBuilder(settings, store, fake)

    first = await builder.build()
    assert first["day_summaries"] == 2
    assert [p["name"] for p in store.knowledge("all", "project")] == ["Billing revamp", "Website launch"]
    assert [p["name"] for p in store.knowledge("public", "project")] == ["Website launch"]  # no DM in the public register

    calls = len(fake.calls)
    second = await builder.build()
    assert second["day_summaries"] == 0 and len(fake.calls) == calls  # nothing changed, no model calls

    store.upsert_message("D1", ts(8), "U1", "billing budget is 12 lakh")
    third = await builder.build()
    assert third["day_summaries"] == 1


def test_a_project_the_model_forgets_is_kept():
    before = [{"name": "Billing revamp"}, {"name": "Website"}, {"name": "New site"}]
    after = [{"name": "Website", "aliases": ["New site"]}]
    assert [p["name"] for p in keep_unmentioned(before, after)] == ["Website", "Billing revamp"]


# ---------------------------------------------------------------------------
# Sign-in
# ---------------------------------------------------------------------------

def test_sign_ins_are_encrypted_and_links_expire():
    secrets = Secrets("k")
    enc = secrets.encrypt("xoxp-secret")
    assert "xoxp" not in enc and secrets.decrypt(enc) == "xoxp-secret"
    assert Secrets("other").decrypt(enc) is None
    state = secrets.make_state(now=1000)
    assert secrets.check_state(state, now=1100)
    assert not secrets.check_state(state, now=1000 + 16 * 60)
    assert not secrets.check_state(state[:-1] + ("0" if state[-1] != "0" else "1"), now=1100)
    assert not Secrets("other").check_state(state, now=1100)


def _app(settings, store, exchange=None) -> TestClient:
    app = create_app(settings=settings)
    app.state.store = store
    app.state.brain = MagicMock(spec=Brain)
    app.state.brain.ask_bot = AsyncMock(return_value="answer")
    app.state.slack_service = None
    if exchange:
        app.state.oauth_exchange = exchange
    return TestClient(app, follow_redirects=False)


def test_the_sign_in_flow_keeps_the_token_encrypted(settings, store):
    store.set_meta("team_id", "T1")

    async def exchange(_settings, code):
        assert code == "good"
        return {"ok": True, "team": {"id": "T1"}, "authed_user": {"id": "U2", "access_token": "xoxp-ravi", "scope": "im:history"}}

    with _app(settings, store, exchange) as client:
        start = client.get("/api/v1/slack/connect")
        assert start.status_code == 302
        location = start.headers["location"]
        assert location.startswith("https://slack.com/oauth/v2/authorize?") and "im%3Ahistory" in location
        state = location.split("state=")[1]
        assert client.get("/api/v1/slack/oauth/callback", params={"code": "good", "state": "forged.1.x"}).status_code == 400
        done = client.get("/api/v1/slack/oauth/callback", params={"code": "good", "state": state})
        assert done.status_code == 200 and "Connected" in done.text
    row = store.tokens()[0]
    assert row["user_id"] == "U2" and "xoxp" not in row["token_enc"]
    assert Secrets(settings.token_key).decrypt(row["token_enc"]) == "xoxp-ravi"


def test_a_sign_in_from_another_workspace_is_refused(settings, store):
    store.set_meta("team_id", "T1")

    async def exchange(_settings, code):
        return {"ok": True, "team": {"id": "T9"}, "authed_user": {"id": "U2", "access_token": "xoxp-x"}}

    with _app(settings, store, exchange) as client:
        state = Secrets(settings.token_key).make_state()
        resp = client.get("/api/v1/slack/oauth/callback", params={"code": "c", "state": state})
    assert resp.status_code == 400 and store.tokens() == []


def test_sign_in_says_what_is_missing(tmp_path, store):
    bare = Settings(data_dir=str(tmp_path / "d"), slack_client_id="", slack_client_secret="",
                    public_url="", token_key="")
    with _app(bare, store) as client:
        resp = client.get("/api/v1/slack/connect")
    assert resp.status_code == 503
    assert "SLACK_CLIENT_ID" in resp.text and "TOKEN_KEY" in resp.text


def test_admin_routes_need_the_key_and_ask_uses_it(settings, store):
    with _app(settings, store) as client:
        assert client.get("/api/v1/memory/status").status_code == 403
        assert client.get("/api/v1/slack/connections").status_code == 403
        assert client.get("/api/v1/memory/status", headers={"X-API-Key": "admin-key"}).status_code == 200
        client.post("/api/v1/ask", json={"question": "projects?"})
        client.post("/api/v1/ask", json={"question": "projects?"}, headers={"X-API-Key": "admin-key"})
        scopes = [c.kwargs["scope"] for c in client.app.state.brain.ask_bot.call_args_list]
    assert scopes == ["public", "all"]
