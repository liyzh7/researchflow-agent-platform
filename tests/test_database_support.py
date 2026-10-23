"""Dedicated integration configuration fails closed without connection attempts."""

import pytest

from researchflow.core.config import Settings
from tests.database_support import database_settings, validate_test_database


def test_database_settings_require_explicit_connection_variables(monkeypatch):
    for suffix in ("HOST", "PORT", "USER", "PASSWORD"):
        monkeypatch.delenv("RESEARCHFLOW_TEST_DATABASE_" + suffix, raising=False)
    with pytest.raises(ValueError, match="Set explicit"):
        database_settings("researchflow_test")


@pytest.mark.parametrize("database_name", ["researchflow", "postgres", "unrelated_test"])
def test_development_and_unlisted_database_names_are_rejected(database_name):
    settings = Settings(
        _env_file=None,
        environment="test",
        database_password="test-only",
        database_name=database_name,
    )
    with pytest.raises(ValueError, match="allow-listed"):
        validate_test_database(settings)


def test_remote_database_host_is_rejected():
    settings = Settings(
        _env_file=None,
        environment="test",
        database_password="test-only",
        database_name="researchflow_test",
        database_host="example.invalid",
    )
    with pytest.raises(ValueError, match="loopback"):
        validate_test_database(settings)
