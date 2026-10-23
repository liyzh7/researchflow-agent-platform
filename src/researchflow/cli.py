"""Small operational CLI for local ResearchFlow workflows."""

import argparse
import asyncio
import getpass
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from researchflow.agent.checkpoint import postgres_checkpointer
from researchflow.auth.admin import claim_legacy, disable_user, mark_interrupted, require_admin
from researchflow.auth.context import AuthError, actor_scope
from researchflow.auth.service import AuthService
from researchflow.core.asyncio_compat import configure_asyncio_policy
from researchflow.core.config import get_settings
from researchflow.core.logging import configure_logging
from researchflow.db.database import Database
from researchflow.indexing.service import IndexingService
from researchflow.providers.local_embedding import LocalEmbeddingProvider


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="researchflow")
    commands = parser.add_subparsers(dest="command", required=True)

    index = commands.add_parser("index", help="build the pinned embedding index")
    index.add_argument("--username", required=True)
    target = index.add_mutually_exclusive_group()
    target.add_argument("--version-id", type=UUID)
    target.add_argument("--document-key")
    index.add_argument(
        "--all-versions",
        action="store_true",
        help="include historical versions (current versions are the default)",
    )

    commands.add_parser("embedding-info", help="print the configured embedding identity")
    commands.add_parser("agent-setup", help="initialize official checkpoint tables after Alembic")
    create = commands.add_parser(
        "create-user", help="local user creation; password via getpass only"
    )
    create.add_argument("--username", required=True)
    create.add_argument("--admin", action="store_true")
    disable = commands.add_parser("disable-user")
    disable.add_argument("--operator", required=True)
    disable.add_argument("--username", required=True)
    claim = commands.add_parser(
        "claim-legacy", help="explicit confirmed quarantine ownership assignment"
    )
    claim.add_argument("--operator", required=True)
    claim.add_argument("--target-user-id", required=True, type=UUID)
    targets = claim.add_mutually_exclusive_group(required=True)
    targets.add_argument("--document-id", type=UUID)
    targets.add_argument("--run-id", type=UUID)
    claim.add_argument("--backup", required=True, type=Path)
    reconcile = commands.add_parser(
        "mark-call-interrupted", help="only after confirming the worker has exited"
    )
    reconcile.add_argument("--operator", required=True)
    reconcile.add_argument("--call-id", required=True, type=UUID)
    return parser


async def _run_index(arguments: argparse.Namespace) -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    database = Database(settings)
    provider = LocalEmbeddingProvider(settings)
    service = IndexingService(database, provider, settings)
    auth = AuthService(database, settings)
    try:
        principal = await _authenticate_operator(auth, arguments.username)
        with actor_scope(principal):
            try:
                if arguments.version_id is not None:
                    results = [await service.index_version(arguments.version_id)]
                else:
                    results = await service.index_targets(
                        document_key=arguments.document_key,
                        include_historical=arguments.all_versions,
                    )
            finally:
                await auth.revoke(principal, principal.session_id)
    finally:
        await database.dispose()
    print(
        json.dumps(
            [
                {
                    "document_version_id": str(result.document_version_id),
                    "model_config_id": str(result.model_config_id),
                    "status": result.status,
                    "segment_count": result.segment_count,
                    "attempt_count": result.attempt_count,
                }
                for result in results
            ],
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


async def _authenticate_operator(auth, username):
    response = await auth.login(username, getpass.getpass("Password: "), "local_cli")
    return await auth.authenticate(response["access_token"])


async def _run_identity(arguments):
    settings = get_settings()
    database = Database(settings)
    auth = AuthService(database, settings)
    try:
        if arguments.command == "create-user":
            password = getpass.getpass("New password (at least 12 characters): ")
            if getpass.getpass("Confirm password: ") != password:
                raise ValueError("password confirmation did not match")
            user_id = await auth.create_user(arguments.username, password, is_admin=arguments.admin)
            print(f"Created local user {user_id}; no default password was configured.")
            return 0
        principal = await _authenticate_operator(auth, arguments.operator)
        with actor_scope(principal):
            try:
                require_admin()
                if arguments.command == "disable-user":
                    await disable_user(database, arguments.username)
                elif arguments.command == "claim-legacy":
                    if not arguments.backup.resolve().is_file():
                        raise ValueError("an existing trusted local backup is required")
                    resource_id = arguments.document_id or arguments.run_id
                    expected = f"CLAIM {resource_id} TO {arguments.target_user_id}"
                    print("Historical text, hashes, version IDs and indexes will not change.")
                    if (
                        input(f"After obtaining explicit authorization, type {expected}: ")
                        != expected
                    ):
                        raise ValueError("ownership assignment not confirmed")
                    await claim_legacy(
                        database,
                        document_id=arguments.document_id,
                        run_id=arguments.run_id,
                        target_user_id=arguments.target_user_id,
                    )
                else:
                    expected = f"INTERRUPTED {arguments.call_id}"
                    if input(f"Confirm the worker has exited; type {expected}: ") != expected:
                        raise ValueError("interrupted call not confirmed")
                    await mark_interrupted(database, arguments.call_id)
            finally:
                await auth.revoke(principal, principal.session_id)
        print("Local administrative operation completed and audited.")
        return 0
    finally:
        await database.dispose()


def _dispatch(argv: Sequence[str] | None = None) -> int:
    configure_asyncio_policy()
    arguments = _parser().parse_args(argv)
    if arguments.command in {
        "create-user",
        "disable-user",
        "claim-legacy",
        "mark-call-interrupted",
    }:
        return asyncio.run(_run_identity(arguments))
    if arguments.command == "agent-setup":

        async def setup():
            async with postgres_checkpointer(get_settings(), setup=True):
                pass

        asyncio.run(setup())
        print("Agent checkpoint tables initialized in agent_checkpoints.")
        return 0
    if arguments.command == "embedding-info":
        provider = LocalEmbeddingProvider(get_settings())
        print(provider.info.model_dump_json(indent=2))
        return 0
    return asyncio.run(_run_index(arguments))


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return _dispatch(argv)
    except (AuthError, SQLAlchemyError, ValueError) as exc:
        print(
            f"Local operation failed ({type(exc).__name__}); no credentials were printed.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
