"""Guard local Docker tests against targeting any pre-existing development service."""

import json
import os
import re
import subprocess

from researchflow.core.config import Settings
from tests.database_support import validate_test_database

VALIDATION_PROJECT = "researchflow-public-validation-p4"
VALIDATION_CONTAINER = VALIDATION_PROJECT + "-postgres"
VALIDATION_VOLUME = VALIDATION_PROJECT + "-data"


def verified_test_container(settings: Settings) -> str:
    """Return an immutable container ID only after checking the entire test boundary."""
    validate_test_database(settings)
    container = os.environ.get("RESEARCHFLOW_TEST_DOCKER_CONTAINER")
    if container != VALIDATION_CONTAINER:
        raise ValueError("Explicit P4 validation container selection is required")
    if settings.database_host != "127.0.0.1" or settings.database_port != 55443:
        raise ValueError("Docker tests require the dedicated P4 loopback database endpoint")
    # Project only required metadata; never print or read the container's environment.
    projection = (
        '{"id":{{json .Id}},"name":{{json .Name}},'
        '"labels":{{json .Config.Labels}},"mounts":{{json .Mounts}},'
        '"ports":{{json .NetworkSettings.Ports}}}'
    )
    result = subprocess.run(
        ["docker", "container", "inspect", "--format", projection, container],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError("P4 validation container metadata could not be inspected")
    metadata = json.loads(result.stdout)
    labels = metadata.get("labels") or {}
    if (
        metadata.get("name") != "/" + VALIDATION_CONTAINER
        or labels.get("com.docker.compose.project") != VALIDATION_PROJECT
        or labels.get("com.docker.compose.service") != "postgres"
    ):
        raise ValueError("Container identity or Compose ownership is outside P4 validation")
    mounts = [
        item
        for item in metadata.get("mounts", [])
        if item.get("Destination") == "/var/lib/postgresql/data"
    ]
    if (
        len(mounts) != 1
        or mounts[0].get("Type") != "volume"
        or mounts[0].get("Name") != VALIDATION_VOLUME
    ):
        raise ValueError("PostgreSQL data is not on the dedicated P4 validation volume")
    ports = metadata.get("ports") or {}
    if ports.get("5432/tcp") != [{"HostIp": "127.0.0.1", "HostPort": str(settings.database_port)}]:
        raise ValueError("Container port mapping differs from the P4 database settings")
    identifier = metadata.get("id", "")
    if not re.fullmatch(r"[a-f0-9]{64}", identifier):
        raise ValueError("Docker did not return an immutable container identity")
    return identifier


def restart_test_database(settings: Settings) -> None:
    container_id = verified_test_container(settings)
    result = subprocess.run(
        ["docker", "restart", "--time", "10", container_id],
        capture_output=True,
        check=False,
        text=True,
        timeout=90,
    )
    if result.returncode != 0:
        raise RuntimeError("Dedicated P4 database restart failed")
