import pytest
import yaml

from config_validation import validate_config
from statement_filter import latest_statement_flags


def test_project_config_is_valid():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    validate_config(yaml.safe_load((root / "config.yaml").read_text()), root)


def test_storage_path_cannot_escape_project(tmp_path):
    config = {
        "mail": {"imap_server": "mail.example", "port": 993, "folder": "INBOX", "local_filters": {}},
        "storage": {"download_dir": "../outside", "output_dir": "output", "history_file": "downloads/history.json"},
    }
    with pytest.raises(ValueError, match="download_dir"):
        validate_config(config, tmp_path)


def test_latest_flags_requires_rank_for_each_filename():
    with pytest.raises(ValueError, match="長度"):
        latest_statement_flags(["a.pdf"], [])


def test_trusted_sender_policy_requires_domains(tmp_path):
    config = {
        "mail": {"imap_server": "mail.example", "port": 993, "folder": "INBOX",
                 "local_filters": {"require_trusted_sender": True}},
        "storage": {"download_dir": "downloads", "output_dir": "output",
                    "history_file": "downloads/history.json"},
    }
    with pytest.raises(ValueError, match="trusted_sender_domains"):
        validate_config(config, tmp_path)
