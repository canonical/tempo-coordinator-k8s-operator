import asyncio
import logging
import shlex
import subprocess
from pathlib import Path

import pytest
from helpers import (
    APP_NAME,
    WORKER_NAME,
    deploy_distributed_cluster,
    emit_trace,
    get_application_ip,
)
from pytest_operator.plugin import OpsTest

from tempo import Tempo
from tempo_config import TempoRole
from tests.integration.helpers import (
    get_resources,
    get_traces_patiently,
    protocols_endpoints,
)

# FIXME: metrics-generator  goes to error state
# https://github.com/canonical/tempo-worker-k8s-operator/issues/61
ALL_ROLES = [role for role in TempoRole.all_nonmeta() if role != "metrics-generator"]
ALL_WORKERS = [f"{WORKER_NAME}-" + role for role in ALL_ROLES]
S3_INTEGRATOR = "s3-integrator"
TRACEGEN_SCRIPT_PATH = Path() / "scripts" / "tracegen.py"

logger = logging.getLogger(__name__)


@pytest.mark.setup
@pytest.mark.abort_on_fail
async def test_deploy_tempo(ops_test: OpsTest, tempo_charm: Path):
    await ops_test.model.deploy(
        tempo_charm, resources=get_resources("."), application_name=APP_NAME, trust=True
    )
    await deploy_distributed_cluster(ops_test, ALL_ROLES)


# TODO: could extend with optional protocols and always-enable them as needed
@pytest.mark.parametrize("protocol", ("otlp_http",))
async def test_trace_ingestion(ops_test, protocol, nonce):
    tempo_address = await get_application_ip(ops_test, APP_NAME)
    # WHEN we emit a trace
    tempo_ingestion_endpoint = protocols_endpoints.get(protocol).format(tempo_address)
    await emit_trace(tempo_ingestion_endpoint, ops_test, nonce=nonce, verbose=1, proto=protocol)
    # THEN we can verify it's been ingested
    await get_traces_patiently(
        tempo_address,
        service_name=f"tracegen-{protocol}",
        tls=False,
    )


def get_metrics(ip: str, port: int):
    proc = subprocess.run(shlex.split(f"curl {ip}:{port}/metrics"), text=True, capture_output=True)
    return proc.stdout


async def test_metrics_endpoints(ops_test):
    # verify that all worker apps and the coordinator can be scraped for metrics on their application IP
    await asyncio.gather(
        *(
            get_metrics(await get_application_ip(ops_test, app), port=Tempo.tempo_http_server_port)
            for app in (*ALL_WORKERS, APP_NAME)
        )
    )


@pytest.mark.teardown
async def test_teardown(ops_test: OpsTest):
    await asyncio.gather(
        *(ops_test.model.remove_application(worker_name) for worker_name in ALL_WORKERS),
        ops_test.model.remove_application(APP_NAME),
    )
