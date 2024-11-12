import logging
from pathlib import Path

import pytest
import yaml
from helpers import deploy_cluster
from tests.integration.juju import WorkloadStatus

METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
APP_NAME = "tempo"
S3_INTEGRATOR = "s3-integrator"
TRACEGEN_SCRIPT_PATH = Path() / "scripts" / "tracegen.py"

logger = logging.getLogger(__name__)


@pytest.mark.setup
def test_deploy_tempo(tempo_charm: Path, tempo_resources, juju):
    juju.deploy(tempo_charm, resources=tempo_resources, alias=APP_NAME, trust=True)

    juju.wait(
        stop=lambda status: status.all(APP_NAME, WorkloadStatus.active),
        # coordinator will be blocked on s3 and workers integration
        timeout=10000,
    )


def test_scale_tempo_up_without_s3_blocks(juju):
    juju.cli("add-unit", APP_NAME, 1)
    juju.wait(
        stop=lambda status: status.all(APP_NAME, WorkloadStatus.blocked),
        timeout=1000,
    )


@pytest.mark.setup
def test_tempo_active_when_deploy_s3_and_workers(juju):
    deploy_cluster(juju)


@pytest.mark.teardown
def test_tempo_blocks_if_s3_goes_away(juju):
    juju.cli("remove-application", S3_INTEGRATOR, "--remove-storage=true")
    juju.wait(
        stop=lambda status: status.all(APP_NAME, WorkloadStatus.blocked),
        timeout=1000,
    )
