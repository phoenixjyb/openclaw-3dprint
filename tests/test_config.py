"""Tests for config loading (uses env vars, no file needed)."""


def test_config_loads_from_env(monkeypatch):
    """Settings should load from environment variables."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-123")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("MESHY_API_KEY", "msy-test")
    monkeypatch.setenv("WINDOWS_HOST", "192.168.1.100")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111,222,333")

    from pipeline.utils.config import Settings

    s = Settings()  # type: ignore[call-arg]
    assert s.telegram_bot_token == "test-token-123"
    assert s.openai_api_key == "sk-test"
    assert s.windows_host == "192.168.1.100"
    assert s.allowed_user_ids == {111, 222, 333}


def test_allowed_user_ids_empty(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("MESHY_API_KEY", "m")
    monkeypatch.setenv("WINDOWS_HOST", "h")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "")

    from pipeline.utils.config import Settings

    s = Settings()  # type: ignore[call-arg]
    assert s.allowed_user_ids == set()
