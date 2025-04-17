import logging
from pathlib import Path

import jubilant
import pytest
import requests
from jubilant import Juju

from helpers import (
    WORKER_APP,
    api_endpoints,
    deploy_monolithic_cluster,
    emit_trace,
    get_app_ip_address,
    get_tempo_ingressed_endpoint,
    get_tempo_internal_endpoint,
    get_traces,
    get_traces_patiently,
    protocols_endpoints, TRAEFIK_APP, SSC_APP,
)
from tests.integration.helpers import TEMPO_APP


logger = logging.getLogger(__name__)


def get_ingress_proxied_hostname(juju: Juju):
    status = juju.status()
    status_msg = status.apps[TRAEFIK_APP].app_status.message

    # hacky way to get ingress hostname, but it's the safest one.
    if "Serving at" not in status_msg:
        raise RuntimeError(f"Ingressed hostname is not present in {TRAEFIK_APP} status message.")
    return status_msg.replace("Serving at", "").strip()


@pytest.mark.setup
def test_build_and_deploy(juju: Juju, tempo_charm: Path):
    # deploy cluster
    deploy_monolithic_cluster(juju)

    juju.deploy("self-signed-certificates", app=SSC_APP)
    juju.deploy(
        "traefik-k8s", app=TRAEFIK_APP, channel="edge", trust=True
    )
    juju.integrate(
        SSC_APP + ":certificates", TRAEFIK_APP + ":certificates"
    )
    juju.wait(
        lambda status: jubilant.all_active(status, [TEMPO_APP, SSC_APP, TRAEFIK_APP, WORKER_APP]),
        error=jubilant.any_error,
        timeout=2000,
    )


def test_relate_ssc(juju: Juju):
    juju.integrate(TEMPO_APP + ":certificates", SSC_APP + ":certificates")
    juju.wait(
        lambda status: jubilant.all_active(status, [TEMPO_APP, SSC_APP, TRAEFIK_APP, WORKER_APP]),
        error=jubilant.any_error,
        timeout=2000,
    )


def test_verify_trace_http_no_tls_fails(juju: Juju, nonce):
    # IF tempo is related to SSC
    # WHEN we emit an http trace, **unsecured**
    tempo_endpoint = get_tempo_internal_endpoint(juju, tls=False, protocol="otlp_http")
    emit_trace(tempo_endpoint, juju, nonce=nonce)  # this should fail
    # THEN we can verify it's not been ingested
    traces = get_traces(get_app_ip_address(juju, TEMPO_APP))
    assert len(traces) == 0


def test_verify_traces_otlp_http_tls(juju: Juju, nonce):
    protocol = "otlp_http"
    svc_name = f"tracegen-{protocol}"
    tempo_endpoint = get_tempo_internal_endpoint(juju, protocol=protocol, tls=True)
    # WHEN we emit a trace secured with TLS
    emit_trace(
        tempo_endpoint,
        juju,
        nonce=nonce,
        verbose=1,
        proto=protocol,
        use_cert=True,
        service_name=svc_name,
    )
    # THEN we can verify it's been ingested
    get_traces_patiently(get_app_ip_address(juju, TEMPO_APP), service_name=svc_name)


def test_relate_ingress(juju: Juju):
    juju.integrate(TEMPO_APP + ":ingress", TRAEFIK_APP + ":traefik-route")
    juju.wait(
        lambda status: jubilant.all_active(status, [TEMPO_APP, SSC_APP, TRAEFIK_APP, WORKER_APP]),
        error=jubilant.any_error,
        timeout=2000,
    )


def test_force_enable_protocols(juju: Juju):
    config = {f"always_enable_{protocol}": "True" for protocol in list(protocols_endpoints.keys())}

    juju.config(TEMPO_APP, config)
    juju.wait(
        lambda status: jubilant.all_active(status, [TEMPO_APP, WORKER_APP]),
        error=jubilant.any_error,
        timeout=2000,
    )


@pytest.mark.parametrize("protocol", protocols_endpoints.keys())
def test_verify_traces_force_enabled_protocols_tls(juju: Juju, nonce, protocol):
    tempo_host = get_ingress_proxied_hostname(juju)
    logger.info(f"emitting & verifying trace using {protocol} protocol.")

    tempo_endpoint = get_tempo_ingressed_endpoint(
        tempo_host,
        protocol=protocol,
        tls=True,
    )
    # emit a trace secured with TLS
    emit_trace(
        tempo_endpoint,
        juju,
        nonce=nonce,
        verbose=1,
        proto=protocol,
        use_cert=True,
        service_name=f"tracegen-tls-{protocol}",
    )
    # verify it's been ingested
    get_traces_patiently(tempo_host, service_name=f"tracegen-tls-{protocol}")


def test_workload_traces_tls(juju: Juju):
    tempo_host = get_ingress_proxied_hostname(juju)
    # verify traces from tempo-scalable-single-binary are ingested
    assert get_traces_patiently(
        tempo_host,
        service_name="tempo-scalable-single-binary",
    )


@pytest.mark.parametrize(
    "protocol",
    # test all ports on the coordinator
    set(protocols_endpoints.keys()).union(api_endpoints.keys()),
)
def test_plain_request_redirect(juju: Juju, protocol):
    if "grpc" in protocol:
        # there's no simple way to test with a gRPC client
        return
    tempo_host = get_ingress_proxied_hostname(juju)
    tempo_endpoint = get_tempo_ingressed_endpoint(tempo_host, protocol=protocol, tls=False)
    req = requests.get(
        tempo_endpoint,
        verify=False,
        allow_redirects=False,
    )
    # Permanent Redirect codes
    assert req.status_code == 301 or req.status_code == 308


@pytest.mark.teardown
def test_remove_relation(juju: Juju):
    juju.juju(
        "remove-relation", TEMPO_APP + ":certificates", SSC_APP + ":certificates"
    )

    # coordinator will be set to blocked since ingress is over TLS, but the coordinator is not
    juju.wait(
        lambda status: jubilant.all_blocked(status, [TEMPO_APP]),
        error=jubilant.any_error,
        timeout=1000,
    )
