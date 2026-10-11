import os
import stat

import pytest

from analyst import llm
from analyst.llm import load_dotenv

REAL = "gsk_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ("GROQ_API_KEY", "XAI_API_KEY", "ANALYST_MODEL", "LABOPS_AUTH", "EVIL_VAR"):
        monkeypatch.delenv(k, raising=False)


def write(tmp_path, text, mode=0o600):
    p = tmp_path / ".env"
    p.write_text(text)
    os.chmod(p, mode)
    return p


def test_loads_keys_and_ignores_comments_quotes_and_blanks(tmp_path):
    p = write(tmp_path, f"# comment\n\nGROQ_API_KEY=\"{REAL}\"\nXAI_API_KEY='xai-{REAL}'  \nnot a line\n")
    assert sorted(load_dotenv(p)) == ["GROQ_API_KEY", "XAI_API_KEY"]
    assert os.environ["GROQ_API_KEY"] == REAL and os.environ["XAI_API_KEY"] == "xai-" + REAL


def test_the_shell_wins_over_the_file(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "from-the-shell-" + REAL)
    assert load_dotenv(write(tmp_path, f"GROQ_API_KEY={REAL}\n")) == []
    assert os.environ["GROQ_API_KEY"].startswith("from-the-shell")


@pytest.mark.parametrize("name", ["PATH", "PYTHONPATH", "LD_PRELOAD", "DYLD_INSERT_LIBRARIES", "HOME", "EVIL_VAR", "ANTHROPIC_BASE_URL", "bad name_API_KEY"])
def test_dangerous_or_unknown_variables_are_never_loaded(tmp_path, monkeypatch, name):
    before = dict(os.environ)
    assert load_dotenv(write(tmp_path, f"{name}=/tmp/evil\n")) == []
    assert dict(os.environ) == before


def test_empty_values_are_ignored_so_the_example_file_is_inert(tmp_path):
    assert load_dotenv(write(tmp_path, "GROQ_API_KEY=\n# XAI_API_KEY=\n")) == [] and "GROQ_API_KEY" not in os.environ


def test_missing_file_is_fine(tmp_path):
    assert load_dotenv(tmp_path / "nope.env") == []


def test_world_readable_env_file_triggers_a_warning(tmp_path, capsys):
    load_dotenv(write(tmp_path, f"GROQ_API_KEY={REAL}\n", mode=0o644))
    assert "chmod 600" in capsys.readouterr().err


def test_a_private_env_file_is_silent(tmp_path, capsys):
    load_dotenv(write(tmp_path, f"GROQ_API_KEY={REAL}\n"))
    assert capsys.readouterr().err == ""


def test_make_client_reads_the_env_file_in_the_current_directory(tmp_path, monkeypatch):
    write(tmp_path, f"GROQ_API_KEY={REAL}\n")
    monkeypatch.chdir(tmp_path)
    client, model = llm.make_client("groq")
    assert client.http.headers["authorization"] == f"Bearer {REAL}"


def test_a_placeholder_in_the_env_file_is_still_rejected(tmp_path, monkeypatch):
    write(tmp_path, "GROQ_API_KEY=paste-your-key-here\n")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit, match="placeholder text"):
        llm.make_client("groq")


def test_missing_key_message_mentions_the_env_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit, match=r"\.env"):
        llm.make_client("groq")
