from redkeep.config import Settings


def test_the_project_key_name_wins(monkeypatch):
    monkeypatch.setenv("REDKEEP_ANTHROPIC_API_KEY", "project")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "shared")
    s = Settings.from_env()
    assert (s.anthropic_api_key, s.anthropic_key_name) == ("project", "REDKEEP_ANTHROPIC_API_KEY")


def test_the_standard_key_name_still_works(monkeypatch):
    monkeypatch.setenv("REDKEEP_ANTHROPIC_API_KEY", "")  # empty counts as unset
    monkeypatch.setenv("ANTHROPIC_API_KEY", "shared")
    s = Settings.from_env()
    assert (s.anthropic_api_key, s.anthropic_key_name) == ("shared", "ANTHROPIC_API_KEY")


def test_no_key(monkeypatch):
    monkeypatch.delenv("REDKEEP_ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    s = Settings.from_env()
    assert s.anthropic_api_key is None and s.anthropic_key_name is None
