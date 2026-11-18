"""Integration-level tests for the Alpha HTTP endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.file_service import FileService
from app.services.groq_service import GroqService


def _make_client(tmp_path, groq_answer: str = "Test answer") -> TestClient:
    settings = Settings(
        groq_api_key="test-key",
        slack_bot_token="",
        slack_app_token="",
        data_dir=str(tmp_path / "data"),
    )
    fs = FileService(data_dir=settings.data_dir)
    fs.ensure_data_dir()

    mock_groq = MagicMock(spec=GroqService)
    mock_groq.ask_bot = AsyncMock(return_value=groq_answer)

    app = create_app(settings=settings)
    app.state.settings = settings
    app.state.file_service = fs
    app.state.groq_service = mock_groq
    app.state.slack_service = None

    client = TestClient(app, raise_server_exceptions=True)
    # Patch state after lifespan to keep our mocks
    client.app.state.file_service = fs
    client.app.state.groq_service = mock_groq
    client.app.state.slack_service = None
    return client


class TestHealthCheck:
    def test_root_returns_ok(self, tmp_path):
        with _make_client(tmp_path) as c:
            resp = c.get("/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "Alpha" in body["app"]

    def test_root_contains_version(self, tmp_path):
        with _make_client(tmp_path) as c:
            resp = c.get("/")
        assert "version" in resp.json()


class TestAskEndpoint:
    def test_ask_returns_answer(self, tmp_path):
        with _make_client(tmp_path, groq_answer="Hello from Alpha") as c:
            resp = c.post("/api/v1/ask", json={"question": "Who are you?"})
        assert resp.status_code == 200
        assert resp.json()["answer"] == "Hello from Alpha"

    def test_ask_missing_question_returns_422(self, tmp_path):
        with _make_client(tmp_path) as c:
            resp = c.post("/api/v1/ask", json={"user": "tester"})
        assert resp.status_code == 422

    def test_ask_empty_question_returns_422(self, tmp_path):
        with _make_client(tmp_path) as c:
            resp = c.post("/api/v1/ask", json={"question": ""})
        assert resp.status_code == 422


class TestMessageEndpoint:
    def test_post_message_returns_saved(self, tmp_path):
        with _make_client(tmp_path) as c:
            resp = c.post(
                "/api/v1/message",
                json={"user": "alice", "text": "Hello workspace!", "channel": "general"},
            )
        assert resp.status_code == 200
        assert resp.json()["saved"] is True

    def test_post_message_missing_text_returns_422(self, tmp_path):
        with _make_client(tmp_path) as c:
            resp = c.post("/api/v1/message", json={"user": "alice"})
        assert resp.status_code == 422


class TestHistoryEndpoint:
    def test_get_history_empty(self, tmp_path):
        with _make_client(tmp_path) as c:
            resp = c.get("/api/v1/history")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/markdown")
        assert resp.text in ("", "(empty)")

    def test_get_history_with_content(self, tmp_path):
        settings = Settings(
            groq_api_key="test-key",
            slack_bot_token="",
            slack_app_token="",
            data_dir=str(tmp_path / "data"),
        )
        fs = FileService(data_dir=settings.data_dir)
        fs.ensure_data_dir()
        fs.save_conversation("api", "tester", "What is 2+2?", "4")

        mock_groq = MagicMock(spec=GroqService)
        mock_groq.ask_bot = AsyncMock(return_value="4")

        app = create_app(settings=settings)
        app.state.settings = settings
        app.state.file_service = fs
        app.state.groq_service = mock_groq
        app.state.slack_service = None

        with TestClient(app) as c:
            c.app.state.file_service = fs
            c.app.state.groq_service = mock_groq
            c.app.state.slack_service = None
            resp = c.get("/api/v1/history")
        assert resp.status_code == 200
        assert "What is 2+2?" in resp.text

    def test_get_slack_data_empty(self, tmp_path):
        with _make_client(tmp_path) as c:
            resp = c.get("/api/v1/slack-data")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/plain")


class TestSyncEndpoint:
    def test_sync_without_slack_returns_400(self, tmp_path):
        with _make_client(tmp_path) as c:
            resp = c.post("/api/v1/sync")
        assert resp.status_code == 400

    def test_404_on_unknown_route(self, tmp_path):
        with _make_client(tmp_path) as c:
            resp = c.get("/api/v1/does-not-exist")
        assert resp.status_code == 404
