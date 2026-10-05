import pytest


@pytest.fixture(autouse=True)
def prevent_real_credentials_in_tests(monkeypatch):
    # Unit tests must never load the user's actual bank credentials from .env.
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
