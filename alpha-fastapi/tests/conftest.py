"""Shared pytest fixtures for Alpha test suite."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.file_service import FileService
from app.services.bedrock_service import Brain


@pytest.fixture
def test_settings(tmp_path) -> Settings:
    return Settings(
        slack_bot_token="",
        slack_app_token="",
        data_dir=str(tmp_path / "data"),
        debug=True,
    )


@pytest.fixture
def file_service(test_settings) -> FileService:
    fs = FileService(data_dir=test_settings.data_dir)
    fs.ensure_data_dir()
    return fs


@pytest.fixture
def mock_groq_service(file_service, test_settings) -> Brain:
    svc = MagicMock(spec=Brain)
    svc.ask_bot = AsyncMock(return_value="Mock answer from Alpha")
    return svc


@pytest.fixture
def client(test_settings, file_service, mock_groq_service) -> TestClient:
    """Return a synchronous TestClient with mocked service state."""
    app = create_app(settings=test_settings)

    # Pre-populate app state so lifespan deps resolve correctly
    app.state.settings = test_settings
    app.state.file_service = file_service
    app.state.brain = mock_groq_service
    app.state.slack_service = None

    # Use lifespan=False so we control state manually
    with TestClient(app, raise_server_exceptions=True) as c:
        # Override state after TestClient starts (lifespan ran)
        c.app.state.file_service = file_service
        c.app.state.brain = mock_groq_service
        c.app.state.slack_service = None
        yield c
