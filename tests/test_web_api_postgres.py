"""Web discovery must preserve owner/capability boundaries and truthful index state."""

import os

import pytest

from tests.test_security_postgres import create_run, secured, upload  # noqa: F401

pytestmark = [
    pytest.mark.security_postgres,
    pytest.mark.skipif(
        os.getenv("RESEARCHFLOW_RUN_SECURITY_POSTGRES") != "1",
        reason="explicit dedicated PostgreSQL security tests",
    ),
]


def test_owned_workflow_discovery_and_reauthentication_recovery(secured):  # noqa: F811
    client, accounts = secured
    alice, bob = accounts["alice"], accounts["bob"]
    document = upload(client, alice)
    run = create_run(client, alice, document)
    path = f"/api/v1/agent/runs/{run['run_id']}"
    assert client.get("/api/v1/agent/runs", headers=bob["headers"]).json()["total"] == 0
    listed = client.get("/api/v1/agent/runs", headers=alice["headers"])
    assert listed.status_code == 200
    assert listed.json()["items"][0]["run_id"] == run["run_id"]
    assert "workflow_token" not in listed.text and "thread_id" not in listed.text
    assert client.post(path + "/access", headers=bob["headers"]).status_code == 404
    recovered = client.post(path + "/access", headers=alice["headers"])
    assert recovered.status_code == 200
    assert recovered.headers["cache-control"] == "no-store"
    assert recovered.json()["workflow_token"] == run["workflow_token"]
    assert client.get(path, headers=alice["headers"]).status_code == 403
    assert (
        client.get(
            path,
            headers={**alice["headers"], "X-Workflow-Token": recovered.json()["workflow_token"]},
        ).status_code
        == 200
    )
    audit = client.get("/api/v1/audit/events?limit=100", headers=alice["headers"]).json()
    assert any(item["event_type"] == "agent.access_recovered" for item in audit["items"])


def test_index_status_query_is_owned_and_persistent(secured):  # noqa: F811
    client, accounts = secured
    value = upload(client, accounts["alice"])
    path = f"/api/v1/documents/{value['document_id']}/versions/{value['version_id']}/index"
    assert client.get(path, headers=accounts["bob"]["headers"]).status_code == 404
    state = client.get(path, headers=accounts["alice"]["headers"])
    assert state.status_code == 200
    assert state.json()["status"] == "completed" and state.json()["segment_count"] > 0
    assert state.json()["updated_at"] is not None
