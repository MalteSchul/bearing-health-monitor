from monitor.config import Settings


def test_api_key_is_read_from_the_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=from-file\nOTHER_TOOL=ignored\n", encoding="utf-8")

    key = Settings(_env_file=env).anthropic_api_key  # type: ignore[call-arg]

    assert key is not None and key.get_secret_value() == "from-file"


def test_environment_variable_wins_over_the_env_file(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-environment")
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=from-file\n", encoding="utf-8")

    key = Settings(_env_file=env).anthropic_api_key  # type: ignore[call-arg]

    assert key is not None and key.get_secret_value() == "from-environment"


def test_api_key_never_shows_in_the_settings_text(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")

    assert "sk-ant-secret" not in repr(Settings())
