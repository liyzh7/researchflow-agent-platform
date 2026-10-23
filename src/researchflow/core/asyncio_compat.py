"""Runtime compatibility required by Psycopg async connections on Windows."""

import asyncio
import sys


def configure_asyncio_policy() -> None:
    """Select the event loop implementation supported by Psycopg on Windows."""

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def selector_loop_factory() -> asyncio.AbstractEventLoop:
    """Create the event loop supported by Psycopg async on Windows."""

    return asyncio.SelectorEventLoop()
