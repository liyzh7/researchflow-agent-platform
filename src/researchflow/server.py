"""Safe local development server entry point."""

import uvicorn


def main() -> None:
    """Run the app on loopback with Psycopg's compatible event loop."""

    uvicorn.run(
        "researchflow.main:app",
        host="127.0.0.1",
        port=8000,
        access_log=False,
        limit_concurrency=32,
        backlog=64,
        timeout_graceful_shutdown=15,
        loop="researchflow.core.asyncio_compat:selector_loop_factory",
    )


if __name__ == "__main__":
    main()
