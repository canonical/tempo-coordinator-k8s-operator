import json
import logging
from pathlib import Path

import pytest

from helpers import WORKER_NAME, deploy_cluster
from tests.integration.helpers import (
    get_traces_patiently,
    APP_NAME,
    TESTER_NAME,
    TESTER_GRPC_NAME,
)
from tests.integration.juju import WorkloadStatus

logger = logging.getLogger(__name__)


@pytest.mark.setup
def test_deploy_testers(
    tempo_charm: Path,
    tempo_resources,
    tester_charm,
    tester_resources,
    tester_grpc_charm,
    tester_grpc_resources,
    juju,
):
    # Given a fresh build of the charm
    # When deploying it together with testers
    # Then applications should eventually be created

    juju.deploy(tempo_charm, resources=tempo_resources, alias=APP_NAME, trust=True)

    juju.deploy(
        tester_charm,
        resources=tester_resources,
        alias=TESTER_NAME,
        scale=3,
    )
    juju.deploy(
        tester_grpc_charm,
        resources=tester_grpc_resources,
        alias=TESTER_GRPC_NAME,
        scale=3,
    )

    deploy_cluster(juju)

    # for both testers, depending on the result of race with tempo it's either waiting or active
    juju.wait(
        stop=lambda status: status.all(
            (TESTER_NAME, TESTER_GRPC_NAME), WorkloadStatus.active
        )
        or status.all((TESTER_NAME, TESTER_GRPC_NAME), WorkloadStatus.waiting),
        timeout=2000,
    )


@pytest.mark.setup
def test_relate(juju):
    # given a deployed charm
    # when relating it together with the tester
    # then relation should appear
    juju.integrate(APP_NAME + ":tracing", TESTER_NAME + ":tracing")
    juju.integrate(APP_NAME + ":tracing", TESTER_GRPC_NAME + ":tracing")
    juju.wait(
        stop=lambda status: status.all(
            (APP_NAME, WORKER_NAME, TESTER_NAME, TESTER_GRPC_NAME),
            WorkloadStatus.active,
        ),
        timeout=1000,
    )


def test_verify_traces_http(juju):
    # given a relation between charms
    # when traces endpoint is queried
    # then it should contain traces from the tester charm
    status = juju.status()
    app_ip_address = status["applications"][APP_NAME]["address"]
    traces = get_traces_patiently(
        tempo_host=app_ip_address, service_name="TempoTesterCharm", tls=False
    )
    assert (
        traces
    ), f"There's no trace of charm exec traces in tempo. {json.dumps(traces, indent=2)}"


@pytest.mark.skip(reason="fails because search query results are not stable")
# keep an eye onhttps://github.com/grafana/tempo/issues/3777 and see if they fix it
def test_verify_buffered_charm_traces_http(juju):
    # given a relation between charms
    # when traces endpoint is queried
    # then it should contain all traces from the tester charm since the setup phase, thanks to the buffer
    status = juju.status()
    app_ip_address = status["applications"][APP_NAME]["address"]
    traces = get_traces_patiently(
        tempo_host=app_ip_address, service_name="TempoTesterCharm", tls=False
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


def test_verify_traces_grpc(juju):
    # the tester-grpc charm emits a single grpc trace in its common exit hook
    # we verify it's there
    status = juju.status()
    app_ip_address = status["applications"][APP_NAME]["address"]
    logger.info(app_ip_address)
    traces = get_traces_patiently(
        tempo_host=app_ip_address, service_name="TempoTesterGrpcCharm", tls=False
    )
    assert traces, f"There's no trace of generated grpc traces in tempo. {json.dumps(traces, indent=2)}"


@pytest.mark.teardown
def test_remove_relation(juju):
    # given related charms
    # when relation is removed
    # then both charms should become active again
    juju.disintegrate(APP_NAME + ":tracing", TESTER_NAME + ":tracing")
    juju.disintegrate(APP_NAME + ":tracing", TESTER_GRPC_NAME + ":tracing")

    juju.wait(
        stop=lambda status: status.all(
            (APP_NAME, TESTER_NAME, TESTER_GRPC_NAME), WorkloadStatus.active
        ),
        timeout=1000,
    )
