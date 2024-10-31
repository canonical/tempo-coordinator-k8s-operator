import json
import logging
from pathlib import Path

import pytest

from conftest import APP_NAME, TESTER_GRPC_NAME, TESTER_NAME
from helpers import WORKER_NAME, deploy_cluster
from juju import Juju
from tests.integration.helpers import get_traces_patiently

logger = logging.getLogger(__name__)


@pytest.mark.setup
@pytest.mark.abort_on_fail
def test_deploy_testers(
    tempo_charm: Path,
    tempo_resources,
    tester_charm,
    tester_resources,
    tester_grpc_charm,
    tester_grpc_resources,
):
    # Given a fresh build of the charm
    # When deploying it together with testers
    # Then applications should eventually be created

    tester_charm = "./tests/integration/tester/"
    tester_grpc_charm = "./tests/integration/tester-grpc/"

    (Juju.deploy(tempo_charm, resources=tempo_resources, alias=APP_NAME, trust=True),)
    (
        Juju.deploy(
            tester_charm,
            resources=tester_resources,
            alias=TESTER_NAME,
            scale=3,
        ),
    )
    Juju.deploy(
        tester_grpc_charm,
        resources=tester_grpc_resources,
        alias=TESTER_GRPC_NAME,
        scale=3,
    )

    deploy_cluster()

    # for both testers, depending on the result of race with tempo it's either waiting or active
    Juju.wait_for_idle(
        applications=[TESTER_NAME, TESTER_GRPC_NAME],
        timeout=2000,
    )


@pytest.mark.setup
@pytest.mark.abort_on_fail
def test_relate():
    # given a deployed charm
    # when relating it together with the tester
    # then relation should appear
    Juju.integrate(APP_NAME + ":tracing", TESTER_NAME + ":tracing")
    Juju.integrate(APP_NAME + ":tracing", TESTER_GRPC_NAME + ":tracing")
    Juju.wait_for_idle(
        applications=[APP_NAME, WORKER_NAME, TESTER_NAME, TESTER_GRPC_NAME],
        timeout=1000,
    )


def test_verify_traces_http():
    # given a relation between charms
    # when traces endpoint is queried
    # then it should contain traces from the tester charm
    status = Juju.status()
    app = status["applications"][APP_NAME]
    traces = get_traces_patiently(
        tempo_host=app.public_address, service_name="TempoTesterCharm", tls=False
    )
    assert (
        traces
    ), f"There's no trace of charm exec traces in tempo. {json.dumps(traces, indent=2)}"


@pytest.mark.skip(reason="fails because search query results are not stable")
# keep an eye onhttps://github.com/grafana/tempo/issues/3777 and see if they fix it
def test_verify_buffered_charm_traces_http():
    # given a relation between charms
    # when traces endpoint is queried
    # then it should contain all traces from the tester charm since the setup phase, thanks to the buffer
    status = Juju.status()
    app = status["applications"][APP_NAME]
    traces = get_traces_patiently(
        tempo_host=app.public_address, service_name="TempoTesterCharm", tls=False
    )

    # charm-tracing trace names are in the format:
    # "mycharm/0: <event-name> event"
    captured_events = {trace["rootTraceName"].split(" ")[1] for trace in traces}
    expected_setup_events = {
        "start",
        "install",
        "leader-elected",
        "tracing-relation-created",
        "replicas-relation-created",
    }
    assert expected_setup_events.issubset(captured_events)


def test_verify_traces_grpc():
    # the tester-grpc charm emits a single grpc trace in its common exit hook
    # we verify it's there
    status = Juju.status()
    app = status["applications"][APP_NAME]
    logger.info(app.public_address)
    traces = get_traces_patiently(
        tempo_host=app.public_address, service_name="TempoTesterGrpcCharm", tls=False
    )
    assert traces, f"There's no trace of generated grpc traces in tempo. {json.dumps(traces, indent=2)}"


@pytest.mark.teardown
@pytest.mark.abort_on_fail
def test_remove_relation():
    # given related charms
    # when relation is removed
    # then both charms should become active again
    Juju.disintegrate(APP_NAME + ":tracing", TESTER_NAME + ":tracing")
    Juju.disintegrate(APP_NAME + ":tracing", TESTER_GRPC_NAME + ":tracing")
    Juju.wait_for_idle(
        applications=[APP_NAME, TESTER_NAME, TESTER_GRPC_NAME], timeout=1000
    )
