"""Trusted local pg_dump/pg_restore, always into a NEW isolated database, never --clean."""

import argparse
import hashlib
import json
import re
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql

from researchflow.core.config import Settings

ROOT = Path(__file__).resolve().parents[1]


def connection(settings, database_name, *, autocommit=False):
    return psycopg.connect(
        host=settings.database_host,
        port=settings.database_port,
        user=settings.database_user,
        password=settings.database_password.get_secret_value(),
        dbname=database_name,
        autocommit=autocommit,
    )


def command(*arguments):
    result = subprocess.run(arguments, capture_output=True, check=False, timeout=180)
    if result.returncode != 0:
        # pg_restore error contexts may include row data: do not echo subprocess stderr.
        raise RuntimeError(
            f"{arguments[0]} operation failed with exit {result.returncode}; "
            "inspect local tooling privately"
        )
    return result.stdout.decode("utf-8", errors="replace")


def manifest(settings, name):
    with connection(settings, name) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        tables = conn.execute("""SELECT schemaname, tablename FROM pg_tables
            WHERE schemaname IN ('public','agent_checkpoints')
            ORDER BY schemaname, tablename""").fetchall()
        rows = {}
        for schema, table in tables:
            # Per-row digests are ordered by digest, independent of physical row order/restore plan.
            query = sql.SQL("""SELECT count(*), md5(coalesce(string_agg(
                md5(to_jsonb(t)::text), '' ORDER BY md5(to_jsonb(t)::text)), ''))
                FROM {}.{} AS t""").format(sql.Identifier(schema), sql.Identifier(table))
            count, digest = conn.execute(query).fetchone()
            rows[f"{schema}.{table}"] = {"rows": count, "row_digest_md5": digest}
        extensions = conn.execute(
            "SELECT extname, extversion FROM pg_extension ORDER BY extname"
        ).fetchall()
        indexes = conn.execute("""SELECT schemaname, tablename, indexname, indexdef FROM pg_indexes
            WHERE schemaname IN ('public','agent_checkpoints')
            ORDER BY schemaname, tablename, indexname""").fetchall()
        return {"tables": rows, "extensions": extensions, "indexes": indexes}


def backup_and_restore(settings, source, target):
    if not re.fullmatch(r"researchflow[A-Za-z0-9_]*", source):
        raise ValueError("unexpected source database name")
    if not re.fullmatch(r"researchflow_restore_[a-z0-9_]+_test", target) or source == target:
        raise ValueError("restore target must be a new researchflow_restore_*_test database")
    with connection(settings, "postgres", autocommit=True) as conn:
        if conn.execute("SELECT 1 FROM pg_database WHERE datname=%s", (target,)).fetchone():
            raise ValueError("refusing to overwrite or recreate an existing database")
    folder = ROOT / ".cache" / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    filename = f"phase6-{source}-{uuid4().hex}.dump"
    output = folder / filename
    container_path = "/tmp/" + filename
    before = manifest(settings, source)
    started = time.perf_counter()
    command(
        "docker",
        "exec",
        "researchflow-public-candidate-postgres",
        "pg_dump",
        "-U",
        settings.database_user,
        "-Fc",
        "--no-owner",
        "--no-privileges",
        "-d",
        source,
        "-f",
        container_path,
    )
    command("docker", "cp", "researchflow-public-candidate-postgres:" + container_path, str(output))
    dump_ms = (time.perf_counter() - started) * 1000
    after_source = manifest(settings, source)
    if before != after_source:
        raise RuntimeError("source changed during backup; cannot claim a stable digest comparison")
    with connection(settings, "postgres", autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(target)))
    started = time.perf_counter()
    command(
        "docker",
        "exec",
        "researchflow-public-candidate-postgres",
        "pg_restore",
        "-U",
        settings.database_user,
        "--exit-on-error",
        "--no-owner",
        "--no-privileges",
        "-d",
        target,
        container_path,
    )
    restore_ms = (time.perf_counter() - started) * 1000
    restored = manifest(settings, target)
    if before != restored:
        raise RuntimeError("restored schema/index/extension/count/digest manifest differs")
    return {
        "source": source,
        "target": target,
        "source_unchanged": True,
        "full_manifest_equal": True,
        "backup_relative_path": str(output.relative_to(ROOT)),
        "backup_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "bytes": output.stat().st_size,
        "dump_ms": round(dump_ms, 3),
        "restore_ms": round(restore_ms, 3),
        "tool_version": command(
            "docker", "exec", "researchflow-public-candidate-postgres", "pg_dump", "--version"
        ).strip(),
        "manifest": before,
        "scope": "trusted local logical backup; contains sensitive data; "
        "no PITR or disaster-recovery claim",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--report", type=Path, required=True)
    arguments = parser.parse_args()
    if arguments.report.exists():
        raise ValueError("refusing to overwrite a prior verification report")
    result = backup_and_restore(Settings(), arguments.source, arguments.target)
    arguments.report.parent.mkdir(parents=True, exist_ok=True)
    arguments.report.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(
        json.dumps(
            {
                key: result[key]
                for key in ("source", "target", "full_manifest_equal", "dump_ms", "restore_ms")
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
