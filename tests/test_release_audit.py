"""Release scanning does not exempt fixtures or emit matched credential material."""

import pytest

from scripts.audit_release import forbidden_path, inspect_bytes


@pytest.mark.parametrize(
    "name",
    [
        ".env",
        "old/.env.local",
        "models/demo.gguf",
        "tests/.auth/state.json",
        "db.dump",
        "local/server.exe",
        "playwright-report/index.html",
    ],
)
def test_sensitive_artifacts_rejected(name):
    assert forbidden_path(name)


@pytest.mark.parametrize("name", [".env.example", "docs/architecture.md", "uv.lock"])
def test_public_candidates_allowed(name):
    assert not forbidden_path(name)


def test_actual_value_is_found_even_in_binary_without_echoing_it():
    synthetic = "synthetic-audit-fixture-not-an-active-credential"
    result = inspect_bytes(b"\0" + synthetic.encode(), [synthetic])
    assert result == [{"type": "configured_secret", "line": None}]
    assert synthetic not in repr(result)


def test_literals_flagged_for_review_not_assumed_real():
    assert inspect_bytes(b'password="synthetic-placeholder"')[0]["type"] == "credential_literal"
    assert inspect_bytes(b'{"session_token":"synthetic-placeholder"}')[0]["type"] == (
        "json_credential_literal"
    )
