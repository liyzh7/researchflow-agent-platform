"""Run real browser login/HTTP against a dedicated DB. Passwords never enter argv/stdout."""

import argparse
import contextlib
import io
import json
import os
import secrets
import signal
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch
from urllib.request import urlopen
from uuid import uuid4

from psycopg import sql

from scripts.backup_restore import connection
from tests.database_support import database_settings
from tests.process_support import python_module

ROOT = Path(__file__).resolve().parents[1]
NODE = os.environ.get("RESEARCHFLOW_NODE", "node")


def stop_owned(process):
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
    else:
        # Only groups created by this harness, never a shared shell/process group.
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        if os.name != "nt":
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def wait_http(url, process):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("validation server exited; inspect ignored local logs")
        try:
            with urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.5)
    raise RuntimeError("validation server startup timed out")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("mock",), default="mock")
    args = parser.parse_args()
    name = "researchflow_web_e2e_test"
    # Local verification may keep dependencies and ephemeral credentials outside the source export.
    frontend = Path(os.environ.get("RESEARCHFLOW_WEB_FRONTEND_DIR", ROOT / "frontend")).resolve()
    cache = Path(
        os.environ.get("RESEARCHFLOW_WEB_CACHE_DIR", ROOT / ".cache" / "web-e2e")
    ).resolve()
    cache.mkdir(parents=True, exist_ok=True)
    settings = database_settings("researchflow_test")
    if settings.environment != "test" or settings.database_host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("Explicit test environment and isolated loopback PostgreSQL required")
    # Existing DBs/volumes are never dropped or reset; every test gets fresh usernames/keys.
    with connection(settings, "postgres", autocommit=True) as conn:
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,)).fetchone():
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    environment = dict(os.environ)
    environment.update(
        {
            "RESEARCHFLOW_ENVIRONMENT": "test",
            "RESEARCHFLOW_DATABASE_NAME": name,
            "RESEARCHFLOW_DATABASE_HOST": settings.database_host,
            "RESEARCHFLOW_DATABASE_PORT": str(settings.database_port),
            "RESEARCHFLOW_DATABASE_USER": settings.database_user,
            "RESEARCHFLOW_DATABASE_PASSWORD": settings.database_password.get_secret_value(),
            "RESEARCHFLOW_AGENT_ENABLED": "true",
            "RESEARCHFLOW_RAG_ENABLED": "true",
            "RESEARCHFLOW_RERANKER_ENABLED": "true",
            "RESEARCHFLOW_AUTH_LOGIN_PER_MINUTE": "120",
            "RESEARCHFLOW_AGENT_ACCESS_SECRET": secrets.token_hex(32),
            "RESEARCHFLOW_EVIDENCE_MIN_RERANK_SCORE": "0",
            "RESEARCHFLOW_EVIDENCE_MIN_QUERY_COVERAGE": "0",
            "RESEARCHFLOW_LLM_PROVIDER_MODE": "mock",
            "RESEARCHFLOW_LLM_BASE_URL": "http://127.0.0.1:18080/v1",
            "RESEARCHFLOW_LLM_MODEL": "Qwen3-4B-Q4_K_M",
            "RESEARCHFLOW_LLM_TIMEOUT_SECONDS": "120",
            "RESEARCHFLOW_WEB_INFERENCE": args.mode,
        }
    )
    os.environ.update(environment)
    subprocess.run(
        python_module("alembic", "upgrade", "head"), cwd=ROOT, env=environment, check=True
    )
    subprocess.run(
        python_module("researchflow.cli", "agent-setup"),
        cwd=ROOT,
        env=environment,
        check=True,
    )
    # Exercise the actual CLI's getpass flow, supplying random values only in the test process.
    from researchflow.cli import main as cli_main

    accounts = {}
    for label in ("alice", "bob"):
        username = "web-" + label + "-" + uuid4().hex[:12]
        password = secrets.token_urlsafe(32)
        with (
            patch.object(sys, "argv", ["researchflow", "create-user", "--username", username]),
            patch("researchflow.cli.getpass.getpass", return_value=password),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            result = cli_main()
            if result not in (None, 0):
                raise RuntimeError("test CLI account creation failed")
        accounts[label] = {"username": username, "password": password}
    credential_file = cache / f"{args.mode}-credentials.json"
    credential_file.write_text(json.dumps(accounts), encoding="utf-8")
    environment["RESEARCHFLOW_E2E_CREDENTIALS"] = str(credential_file)
    environment["RESEARCHFLOW_E2E_MODE"] = args.mode
    environment["RESEARCHFLOW_WEB_CACHE_DIR"] = str(cache)
    environment["RESEARCHFLOW_WEB_INJECT_FAILURES"] = "1"
    servers = []
    logs = []
    try:
        for label, command, directory, health in (
            (
                "api",
                python_module(
                    "uvicorn",
                    "scripts.web_test_server:build",
                    "--factory",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "8000",
                    "--no-access-log",
                    "--loop",
                    "researchflow.core.asyncio_compat:selector_loop_factory",
                ),
                ROOT,
                "http://127.0.0.1:8000/health",
            ),
            (
                "vite",
                [NODE, "node_modules/vite/bin/vite.js", "--host", "127.0.0.1", "--port", "5173"],
                frontend,
                "http://127.0.0.1:5173",
            ),
        ):
            log = (cache / f"{args.mode}-{label}.log").open("w", encoding="utf-8")
            logs.append(log)
            process = subprocess.Popen(
                command,
                cwd=directory,
                env=environment,
                stdout=log,
                stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                start_new_session=os.name != "nt",
            )
            servers.append(process)
            wait_http(health, process)
        environment["RESEARCHFLOW_E2E_API_PID"] = str(servers[0].pid)
        environment["RESEARCHFLOW_E2E_PYTHON"] = sys.executable
        result = subprocess.run(
            [NODE, "node_modules/@playwright/test/cli.js", "test", "--project=" + args.mode],
            cwd=frontend,
            env=environment,
            check=False,
        )
        raise SystemExit(result.returncode)
    finally:
        for process in reversed(servers):
            stop_owned(process)
        for log in logs:
            log.close()
        credential_file.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
