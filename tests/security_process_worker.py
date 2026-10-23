"""Only the test process can trigger this hard exit; no HTTP fault switch exists."""

import json
import os
import sys

from fastapi.testclient import TestClient

from tests.agent_support import workflow_app, workflow_settings


class ExitProvider:
    name = "test-only-hard-exit"

    async def generate(self, request, *, timeout_seconds):
        os._exit(74)


def main():
    command = json.load(sys.stdin)
    settings = workflow_settings().model_copy(
        update={"database_name": "researchflow_security_test"}
    )
    with TestClient(workflow_app(settings)) as client:
        client.app.state.llm_provider.inner = ExitProvider()
        client.post(
            "/api/v1/chat",
            headers=command["headers"],
            json={"question": "invented tray process-interruption request"},
        )


if __name__ == "__main__":
    main()
