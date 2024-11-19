import logging
from pathlib import Path

import pytest
import yaml
from helpers import (
    WORKER_NAME,
    deploy_cluster,
    emit_trace,
    get_traces,
    get_traces_patiently,
    protocols_endpoints,
)

from tests.integration.juju import Juju, WorkloadStatus

METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
APP_NAME = "tempo"
SSC = "self-signed-certificates"
SSC_APP_NAME = "ssc"
TRAEFIK = "traefik-k8s"
TRAEFIK_APP_NAME = "trfk"
TRACEGEN_SCRIPT_PATH = Path().absolute() / "scripts" / "tracegen.py"

logger = logging.getLogger(__name__)


def get_ingress_proxied_hostname(juju):
    status = juju.get_status()
    app = status["applications"][TRAEFIK_APP_NAME]
    status_msg = app["status"]["info"]

    # hacky way to get ingress hostname
    if "Serving at" not in status_msg:
        assert (
            False
        ), f"Ingressed hostname is not present in {TRAEFIK_APP_NAME} status message."
    return status_msg.replace("Serving at", "").strip()


def get_tempo_ingressed_endpoint(hostname, protocol):
    protocol_endpoint = protocols_endpoints.get(protocol)
    if protocol_endpoint is None:
        assert False, f"Invalid {protocol}"
    return protocol_endpoint.format(hostname)


def get_tempo_traces_internal_endpoint(protocol, juju):
    hostname = (
        f"{APP_NAME}-0.{APP_NAME}-endpoints.{juju.model_name()}.svc.cluster.local"
    )
    protocol_endpoint = protocols_endpoints.get(protocol)
    if protocol_endpoint is None:
        assert False, f"Invalid {protocol}"
    return protocol_endpoint.format(hostname)


@pytest.mark.setup
def test_build_and_deploy(tempo_charm: Path, juju, tempo_resources):
    juju.deploy(tempo_charm, resources=tempo_resources, alias=APP_NAME, trust=True)
    juju.deploy(SSC, alias=SSC_APP_NAME)
    juju.deploy(TRAEFIK, alias=TRAEFIK_APP_NAME, channel="edge", trust=True)

    juju.integrate(SSC_APP_NAME + ":certificates", TRAEFIK_APP_NAME + ":certificates")
    # deploy cluster
    deploy_cluster(juju)

    juju.wait(
        stop=lambda status: status.all_workloads(
            (APP_NAME, SSC_APP_NAME, TRAEFIK_APP_NAME, WORKER_NAME),
            WorkloadStatus.active,
        ),
        timeout=2000,
    )


def test_relate_ssc(juju):
    juju.integrate(APP_NAME + ":certificates", SSC_APP_NAME + ":certificates")
    juju.wait(
        stop=lambda status: status.all_workloads(
            (APP_NAME, SSC_APP_NAME, TRAEFIK_APP_NAME, WORKER_NAME),
            WorkloadStatus.active,
        ),
        timeout=1000,
    )


def test_push_tracegen_script_and_deps(juju):
    juju.scp(f"{APP_NAME}/0", TRACEGEN_SCRIPT_PATH)
    juju.ssh(
        f"{APP_NAME}/0",
        "python3 -m pip install protobuf==3.20.* opentelemetry-exporter-otlp-proto-grpc opentelemetry-exporter-otlp-proto-http"
        + " opentelemetry-exporter-zipkin opentelemetry-exporter-jaeger",
    )


def test_verify_trace_http_no_tls_fails(server_cert, nonce, juju):
    # IF tempo is related to SSC
    # WHEN we emit an http trace, **unsecured**
    tempo_endpoint = get_tempo_traces_internal_endpoint(juju=juju, protocol="otlp_http")
    emit_trace(tempo_endpoint, juju, nonce=nonce)  # this should fail
    # THEN we can verify it's not been ingested
    traces = get_traces(juju.status().get_application_ip(APP_NAME))
    assert len(traces) == 0


def test_verify_traces_otlp_http_tls(nonce, juju):
    protocol = "otlp_http"
    tempo_endpoint = get_tempo_traces_internal_endpoint(juju=juju, protocol=protocol)
    # WHEN we emit a trace secured with TLS
    emit_trace(
        tempo_endpoint, juju, nonce=nonce, verbose=1, proto=protocol, use_cert=True
    )
    # THEN we can verify it's been ingested
    get_traces_patiently(
        juju.status().get_application_ip(APP_NAME),
        service_name=f"tracegen-{protocol}",
    )


def test_relate_ingress(juju):
    juju.integrate(APP_NAME + ":ingress", TRAEFIK_APP_NAME + ":traefik-route")
    juju.wait(
        stop=lambda status: status.all_workloads(
            (APP_NAME, SSC_APP_NAME, TRAEFIK_APP_NAME, WORKER_NAME),
            WorkloadStatus.active,
        ),
        timeout=1000,
    )


def test_force_enable_protocols(juju: Juju):
    config = {}
    for protocol in list(protocols_endpoints.keys()):
        config[f"always_enable_{protocol}"] = "True"

    juju.application_config_set(APP_NAME, config)
    juju.wait(
        stop=lambda status: status.all_workloads(
            (APP_NAME, WORKER_NAME), WorkloadStatus.active
        ),
        timeout=1000,
    )


@pytest.mark.parametrize("protocol", protocols_endpoints.keys())
def test_verify_traces_force_enabled_protocols_tls(nonce, protocol, juju):
    tempo_host = get_ingress_proxied_hostname(juju)
    logger.info(f"emitting & verifying trace using {protocol} protocol.")
    tempo_endpoint = get_tempo_ingressed_endpoint(tempo_host, protocol=protocol)
    # emit a trace secured with TLS
    emit_trace(
        tempo_endpoint, juju, nonce=nonce, verbose=1, proto=protocol, use_cert=True
    )
    # verify it's been ingested
    get_traces_patiently(tempo_host, service_name=f"tracegen-{protocol}")


@pytest.mark.teardown
def test_remove_relation(juju):
    juju.disintegrate(APP_NAME + ":certificates", SSC_APP_NAME + ":certificates")
    juju.wait(
        stop=lambda status: status.all_workloads((APP_NAME,), WorkloadStatus.active),
        timeout=1000,
    )
