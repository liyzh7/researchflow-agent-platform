"""Separate-process integration harness. Fault controls are not application/API features."""

import json
import os
import sys

from tests.agent_support import workflow_app
from tests.auth_support import AuthenticatedTestClient as TestClient


def main():
    command = json.load(sys.stdin)
    with TestClient(workflow_app(), raise_server_exceptions=False) as client:
        fault = command.get("fault")
        if fault:

            def crash(point):
                if point == fault:
                    os._exit(73)

            client.app.state.agent_service.repository.fault_hook = crash
        response = client.request(
            command["method"],
            command["path"],
            json=command.get("body"),
            headers=command.get("headers", {}),
        )
        print(json.dumps({"status_code": response.status_code, "body": response.json()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
