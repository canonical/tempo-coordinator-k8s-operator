import json
import shlex
import subprocess
from pathlib import Path

import jubilant
import pytest
import yaml
from jubilant import Juju

from helpers import WORKER_APP, deploy_monolithic_cluster, TEMPO_APP
from tests.integration.helpers import get_traces_patiently, get_app_ip_address

TESTER_METADATA = yaml.safe_load(Path("./tests/integration/tester/metadata.yaml").read_text())
TESTER_APP_NAME = TESTER_METADATA["name"]
TESTER_GRPC_METADATA = yaml.safe_load(
    Path("./tests/integration/tester-grpc/metadata.yaml").read_text()
)
TESTER_GRPC_APP_NAME = TESTER_GRPC_METADATA["name"]



@pytest.mark.setup
def test_build_deploy_tester(juju: Juju):
    tester_root = "./tests/integration/tester/"
    proc = subprocess.run(shlex.split("charmcraft pack -p %s" % tester_root),
                          check=True, capture_output=True, text=True)
    tester_charm = proc.stderr.strip().splitlines()[-1].split()[-1]
    resources_tester = {"workload": TESTER_METADATA["resources"]["workload"]["upstream-source"]}

    juju.deploy(
        "./"+tester_charm,
        resources=resources_tester,
        app=TESTER_APP_NAME,
        num_units=3,
    )

@pytest.mark.setup
def test_build_deploy_tester_grpc(juju: Juju):
    tester_grpc_root = "./tests/integration/tester-grpc/"
    proc = subprocess.run(shlex.split("charmcraft pack -p %s" % tester_grpc_root),
                          check=True, capture_output=True, text=True)
    tester_grpc_charm = proc.stderr.strip().splitlines()[-1].split()[-1]
    resources_tester_grpc = {
        "workload": TESTER_GRPC_METADATA["resources"]["workload"]["upstream-source"]
    }

    juju.deploy(
        "./"+tester_grpc_charm,
        resources=resources_tester_grpc,
        app=TESTER_GRPC_APP_NAME,
        num_units=3,
    )


@pytest.mark.setup
def test_deploy_monolithic_cluster(juju: Juju, tempo_charm: Path):
    # Given a fresh build of the charm
    # When deploying it together with testers
    # Then applications should eventually be created
    deploy_monolithic_cluster(juju)

    # for both testers, depending on the result of a race with tempo it'll be waiting for a bit, or active
    juju.wait(
        lambda status: all(status.apps[app].is_active for app in [TESTER_APP_NAME, TEMPO_APP, TESTER_GRPC_APP_NAME]),
        timeout=2000,
        error=jubilant.any_blocked
    )


@pytest.mark.setup
def test_relate(juju: Juju):
    # given a deployed charm
    # when relating it together with the tester
    # then relation should appear
    juju.integrate(TEMPO_APP + ":tracing", TESTER_APP_NAME + ":tracing")
    juju.integrate(TEMPO_APP + ":tracing", TESTER_GRPC_APP_NAME + ":tracing")

    juju.wait(
        lambda status: all(
            status.apps[app].is_active for app in [TEMPO_APP, WORKER_APP, TESTER_APP_NAME, TESTER_GRPC_APP_NAME]),
        timeout=1000
    )


def test_verify_traces_http(juju: Juju):
    # given a relation between charms
    # when traces endpoint is queried
    # then it should contain traces from the tester charm
    app_ip = get_app_ip_address(juju, TEMPO_APP)
    traces = get_traces_patiently(
        tempo_host=app_ip, service_name="TempoTesterCharm", tls=False
    )
    assert (
        traces
    ), f"There's no trace of charm exec traces in tempo. {json.dumps(traces, indent=2)}"


@pytest.mark.skip(reason="fails because search query results are not stable")
# keep an eye onhttps://github.com/grafana/tempo/issues/3777 and see if they fix it
def test_verify_buffered_charm_traces_http(juju: Juju):
    # given a relation between charms
    # when traces endpoint is queried
    # then it should contain all traces from the tester charm since the setup phase, thanks to the buffer
    traces = get_traces_patiently(
        tempo_host=get_app_ip_address(juju, TEMPO_APP), service_name="TempoTesterCharm", tls=False
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


def test_verify_traces_grpc(juju: Juju):
    # the tester-grpc charm emits a single grpc trace in its common exit hook
    # we verify it's there
    traces = get_traces_patiently(
        tempo_host=get_app_ip_address(juju, TEMPO_APP), service_name="TempoTesterGrpcCharm", tls=False
    )
    assert (
        traces
    ), f"There's no trace of generated grpc traces in tempo. {json.dumps(traces, indent=2)}"


@pytest.mark.teardown
def test_remove_relation(juju: Juju):
    # given related charms
    # when relation is removed
    # then both charms should become active again
    juju.remove_relation(TEMPO_APP + ":tracing", TESTER_APP_NAME + ":tracing")
    juju.remove_relation(
        TEMPO_APP + ":tracing", TESTER_GRPC_APP_NAME + ":tracing"
    )

    juju.wait(
        lambda status: status.apps[TEMPO_APP].is_active,
        timeout=1000
    )

    juju.wait(
        lambda status: jubilant.all_active(status, (TEMPO_APP,)),
        error=lambda status: jubilant.any_blocked(status, (TEMPO_APP,)),
        timeout=1000
    ),
    # for tester, depending on the result of race with tempo it's either waiting or active
    juju.wait(
        lambda status: status.apps[TEMPO_APP].is_active,
        timeout=1000,
        error=lambda status: jubilant.any_blocked(status, (TESTER_APP_NAME, TESTER_GRPC_APP_NAME))
    )
