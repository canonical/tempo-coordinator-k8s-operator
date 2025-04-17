import logging
from pathlib import Path

import pytest
import yaml
from jubilant import Juju, all_blocked

from helpers import deploy_monolithic_cluster, TEMPO_APP
from juju.application import Application

from tests.integration.helpers import TEMPO_RESOURCES

METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
APP_NAME = "tempo"
S3_INTEGRATOR = "s3-integrator"

logger = logging.getLogger(__name__)


@pytest.mark.setup
def test_deploy_tempo(juju:Juju, tempo_charm: Path):
    juju.deploy(
        tempo_charm, TEMPO_APP, resources=TEMPO_RESOURCES, trust=True
    )

    # coordinator will be blocked because of missing s3 and workers integration
    juju.wait(
        lambda status: all_blocked(status, (APP_NAME, )),
        timeout=1000
    )

def test_scale_tempo_up_stays_blocked(juju:Juju):
    juju.cli("add-unit", APP_NAME, "-n", "1")
    juju.wait(
        lambda status: all_blocked(status, (APP_NAME, )),
        timeout=1000
    )


@pytest.mark.setup
def test_tempo_active_when_deploy_s3_and_workers(juju:Juju):
    deploy_monolithic_cluster(juju, tempo_deployed_as=TEMPO_APP)


@pytest.mark.teardown
def test_tempo_blocks_if_s3_goes_away(juju:Juju):
    app: Application = juju.model.applications[S3_INTEGRATOR]
    app.destroy(destroy_storage=True)
    juju.model.wait_for_idle(
        apps=[APP_NAME],
        status="blocked",
        timeout=1000,
    )
