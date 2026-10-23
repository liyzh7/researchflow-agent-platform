"""Official PostgreSQL saver with an explicit safe deserialization policy."""

from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from researchflow.core.config import Settings
from researchflow.observability.tracing import operation


class ObservedPostgresSaver(AsyncPostgresSaver):
    async def aget_tuple(self, *args, **kwargs):
        with operation("checkpoint.read"):
            return await super().aget_tuple(*args, **kwargs)

    async def aput(self, *args, **kwargs):
        with operation("checkpoint.write"):
            return await super().aput(*args, **kwargs)

    async def aput_writes(self, *args, **kwargs):
        with operation("checkpoint.pending_writes"):
            return await super().aput_writes(*args, **kwargs)


def strict_serializer() -> JsonPlusSerializer:
    # Explicit allowlist works regardless of environment/import order. No pickle fallback.
    return JsonPlusSerializer(
        pickle_fallback=False, allowed_msgpack_modules=None, allowed_json_modules=None
    )


@asynccontextmanager
async def postgres_checkpointer(settings: Settings, *, setup: bool = False):
    conninfo = make_conninfo(
        host=settings.database_host,
        port=settings.database_port,
        user=settings.database_user,
        password=settings.database_password.get_secret_value(),
        dbname=settings.database_name,
        connect_timeout=settings.database_connect_timeout_seconds,
        options="-c search_path=agent_checkpoints -c statement_timeout=15000",
    )
    async with AsyncConnectionPool(
        conninfo,
        min_size=1,
        max_size=4,
        open=False,
        kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": 0},
        timeout=5,
    ) as pool:
        await pool.wait(timeout=10)
        saver = ObservedPostgresSaver(pool, serde=strict_serializer())
        if setup:
            await saver.setup()
        yield saver
