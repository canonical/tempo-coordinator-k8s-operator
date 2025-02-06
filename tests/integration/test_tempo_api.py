import json
import logging
from pathlib import Path

import pytest
import yaml
from helpers import deploy_cluster
from pytest_operator.plugin import OpsTest

METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
APP_NAME = "tempo"
TESTER_METADATA = yaml.safe_load(
    Path("./tests/integration/tempo-api-tester/charmcraft.yaml").read_text()
)
TESTER_APP_NAME = TESTER_METADATA["name"]


logger = logging.getLogger(__name__)


@pytest.mark.setup
@pytest.mark.abort_on_fail
async def test_build_deploy_api_tester(ops_test: OpsTest, tempo_api_requirer_tester_charm):
    # Given a fresh build of the charm
    # When deploying it together with testers
    # Then applications should eventually be created
    tester_charm = tempo_api_requirer_tester_charm
    await ops_test.model.deploy(tester_charm, application_name=TESTER_APP_NAME)

    await deploy_cluster(ops_test)


async def test_tempo_api_relation(ops_test: OpsTest):
    """Test the tempo-api relation works as expected in attachment."""

    tester_application = ops_test.model.applications[TESTER_APP_NAME]
    await ops_test.model.add_relation(APP_NAME, TESTER_APP_NAME)

    # Wait for the relation to be established
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME], status="active", raise_on_blocked=True, timeout=60
    )

    actual = await get_tester_data(tester_application)
    assert actual.get("grpc_port", None)
    assert actual.get("ingress_url", None)
    assert actual.get("internal_url", None)


async def test_tempo_api_relation_removal(ops_test: OpsTest):
    """Test the tempo-api relation works as expected in removal."""
    # Remove the relation and confirm the data is gone
    await ops_test.model.applications[APP_NAME].remove_relation(
        f"{APP_NAME}:tempo-api", TESTER_APP_NAME
    )
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME], status="active", raise_on_blocked=True, timeout=60
    )

    tester_application = ops_test.model.applications[TESTER_APP_NAME]
    actual = await get_tester_data(tester_application)
    assert actual == {}


async def get_tester_data(tester_application):
    # Check the relation data
    action = await tester_application.units[0].run_action(
        "get-metadata",
    )
    action_result = await action.wait()
    assert action_result.status == "completed"
    return json.loads(action_result.results["relation-data"])
