"""Explicit loopback-only database settings for candidate integration tests.

No dotenv or application/development connection variables are consulted here.
A new disposable test service must be selected with RESEARCHFLOW_TEST_DATABASE_*.
"""

import os

from researchflow.core.config import Settings

TEST_DATABASE_NAMES = frozenset(
    {"researchflow_test", "researchflow_agent_test", "researchflow_security_test"}
)


def validate_test_database(settings: Settings) -> None:
    if settings.environment != "test" or settings.database_name not in TEST_DATABASE_NAMES:
        raise ValueError("Integration connection must use a dedicated allow-listed test database")
    if settings.database_host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Integration connections must remain on loopback")


def database_settings(database_name: str, **overrides) -> Settings:
    names = ("HOST", "PORT", "USER", "PASSWORD")
    missing = [name for name in names if not os.getenv("RESEARCHFLOW_TEST_DATABASE_" + name)]
    if missing:
        raise ValueError(
            "Set explicit RESEARCHFLOW_TEST_DATABASE_* connection values before opting in"
        )
    settings = Settings(
        _env_file=None,
        environment="test",
        database_host=os.environ["RESEARCHFLOW_TEST_DATABASE_HOST"],
        database_port=int(os.environ["RESEARCHFLOW_TEST_DATABASE_PORT"]),
        database_user=os.environ["RESEARCHFLOW_TEST_DATABASE_USER"],
        database_password=os.environ["RESEARCHFLOW_TEST_DATABASE_PASSWORD"],
        database_name=database_name,
        embedding_local_files_only=True,
        reranker_local_files_only=True,
        llm_provider_mode="mock",
        llm_allow_remote=False,
        **overrides,
    )
    validate_test_database(settings)
    return settings
