import logging
from pathlib import Path

import pytest
import yaml
from helpers import deploy_cluster

from tests.integration.juju import Juju

METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
APP_NAME = "tempo"
S3_INTEGRATOR = "s3-integrator"
TRACEGEN_SCRIPT_PATH = Path() / "scripts" / "tracegen.py"

logger = logging.getLogger(__name__)


@pytest.mark.setup
def test_deploy_tempo(tempo_charm: Path, tempo_resources):
    Juju.deploy(tempo_charm, resources=tempo_resources, alias=APP_NAME, trust=True)

    Juju.wait_for_idle(
        applications=[APP_NAME],
        # coordinator will be blocked on s3 and workers integration
        timeout=10000,
    )


def test_scale_tempo_up_without_s3_blocks():
    Juju.cli("add-unit", APP_NAME, 1)
    Juju.wait_for_idle(
        applications=[APP_NAME],
        timeout=1000,
    )


@pytest.mark.setup
def test_tempo_active_when_deploy_s3_and_workers():
    deploy_cluster()


@pytest.mark.teardown
def test_tempo_blocks_if_s3_goes_away():
    Juju.cli("remove-application", S3_INTEGRATOR, "--remove-storage=true")
    Juju.wait_for_idle(
        applications=[APP_NAME],
        status="blocked",
        timeout=1000,
    )
