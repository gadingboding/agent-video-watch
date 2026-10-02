"""WATCH_DETAIL resolution and frame_cap mapping."""
from __future__ import annotations

import config


def test_default_detail_is_balanced(monkeypatch, tmp_path):
    monkeypatch.delenv("WATCH_DETAIL", raising=False)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "missing.env")
    assert config.get_config()["detail"] == "balanced"


def test_env_overrides_detail(monkeypatch, tmp_path):
    monkeypatch.setenv("WATCH_DETAIL", "efficient")
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "missing.env")
    assert config.get_config()["detail"] == "efficient"


def test_invalid_detail_falls_back_to_default(monkeypatch, tmp_path):
    monkeypatch.setenv("WATCH_DETAIL", "bogus")
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "missing.env")
    assert config.get_config()["detail"] == "balanced"


def test_get_config_keys(monkeypatch, tmp_path):
    monkeypatch.delenv("WATCH_DETAIL", raising=False)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "missing.env")
    cfg = config.get_config()
    assert {"detail", "config_file", "whisper_backend", "whisperx_model", "sub_lang"} <= set(cfg)


def test_frame_cap_mapping():
    assert config.frame_cap("efficient") == 50
    assert config.frame_cap("balanced") == 100
    assert config.frame_cap("token-burner") is None
    assert config.frame_cap("transcript") is None
    assert config.frame_cap("anything-else") == 100


import pytest
from pathlib import Path


@pytest.mark.parametrize('value,expected', [
    ('"a#=b" # comment', 'a#=b'), ("'C:\\Users\\a' # note", 'C:\\Users\\a'),
    ('C:\\Users\\a # note', 'C:\\Users\\a'), ('abc#def=ghi', 'abc#def=ghi'),
    ('"$HOME\\n"', '$HOME\\n'),
])
def test_literal_dotenv_values(tmp_path, value, expected):
    path = tmp_path / '.env'
    path.write_text('KEY=' + value)
    assert config.read_env_file(path)['KEY'] == expected


@pytest.mark.parametrize('value', ['"private-secret', '"private-secret" junk'])
def test_malformed_quotes_never_echo_value(tmp_path, value):
    path = tmp_path / '.env'
    path.write_text('KEY=' + value)
    with pytest.raises(config.ConfigError) as exc:
        config.read_env_file(path)
    assert 'line 1' in str(exc.value) and 'private-secret' not in str(exc.value)


def test_provider_preference_precedes_location(monkeypatch):
    Path('.env').write_text('GROQ_API_KEY=project-test\n')
    monkeypatch.setenv('OPENAI_API_KEY', 'environment-test')
    assert config.load_api_key() == ('groq', 'project-test')
    assert config.load_api_key('openai') == ('openai', 'environment-test')


def test_user_file_beats_project_file(monkeypatch):
    config.write_settings({'GROQ_API_KEY': 'user-test'})
    Path('.env').write_text('GROQ_API_KEY=project-test\n')
    assert config.load_api_key() == ('groq', 'user-test')
    monkeypatch.setenv('GROQ_API_KEY', 'environment-test')
    assert config.load_api_key() == ('groq', 'environment-test')
