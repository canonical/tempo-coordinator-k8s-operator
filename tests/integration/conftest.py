# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.
import json
import logging
import os
import random
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from subprocess import check_output

import pytest
from pytest import fixture

from tests.integration.helpers import get_relation_data

APP_NAME = "tempo"
TESTER_NAME = "tester"
TESTER_GRPC_NAME = "tester-grpc"

SSC = "self-signed-certificates"
SSC_APP_NAME = "ssc"

logger = logging.getLogger(__name__)
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import subprocess
from pathlib import Path

import yaml
from pytest import fixture

from juju import Juju

@fixture(scope="module")
def tempo_charm(request):
    """Tempo charm used for integration testing."""
    charm_file = request.config.getoption("--charm-path")
    if charm_file:
        return charm_file

    subprocess.run(
        ["/snap/bin/charmcraft", "pack", "--verbose"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return next(Path.glob(Path("."), "*.charm")).absolute()


@fixture(scope="module")
def tester_charm(request):
    """Tempo charm used for integration testing."""
    charm_file = request.config.getoption("--tester-path")
    if charm_file:
        return charm_file
    path = './tests/integration/tester/'
    subprocess.run(
        ["/snap/bin/charmcraft", "pack", "--verbose", "-p", path],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return next(Path.glob(Path(path), "*.charm")).absolute()


@fixture(scope="module")
def tester_grpc_charm(request):
    """Tempo charm used for integration testing."""
    charm_file = request.config.getoption("--tester-grpc-path")
    if charm_file:
        return charm_file
    path = './tests/integration/tester-grpc/'
    subprocess.run(
        ["/snap/bin/charmcraft", "pack", "--verbose", "-p", path],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return next(Path.glob(Path(path), "*.charm")).absolute()


@pytest.fixture(scope="session")
def tempo_resources():
    meta = yaml.safe_load(Path("./charmcraft.yaml").read_text())
    return {
        "nginx-image": meta["resources"]["nginx-image"]["upstream-source"],
        "nginx-prometheus-exporter-image": meta["resources"][
            "nginx-prometheus-exporter-image"
        ]["upstream-source"],
    }


@pytest.fixture(scope="session")
def tester_resources():
    meta = yaml.safe_load(Path("./tests/integration/tester/metadata.yaml").read_text())
    return {"workload": meta["resources"]["workload"]["upstream-source"]}


@pytest.fixture(scope="session")
def tester_grpc_resources():
    meta = yaml.safe_load(
        Path("./tests/integration/tester-grpc/metadata.yaml").read_text()
    )
    return{
        "workload": meta["resources"]["workload"]["upstream-source"]
    }


@fixture(scope="module")
def tempo_oci_image():
    meta = yaml.safe_load(Path("./charmcraft.yaml").read_text())
    return meta["resources"]["zinc-image"]["upstream-source"]


@fixture(scope="module")
def traefik_lb_ip():
    model_name = Juju.status()["model"]["name"]
    proc = subprocess.run(
        [
            "/snap/bin/kubectl",
            "-n",
            model_name,
            "get",
            "service",
            f"{TRAEFIK}-lb",
            "-o=jsonpath='{.status.loadBalancer.ingress[0].ip}'",
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    ip_address = proc.stdout.strip("'")
    return ip_address

@fixture(scope="session")
def tempo_charm():
    """Tempo charm used for integration testing.

    Build once per session and reuse it in all integration tests to save some minutes/hours.
    You can also set `TEMPO_CHARM` env variable to use an already existing built charm.
    """
    if tempo_charm := os.getenv("TEMPO_CHARM"):
        return tempo_charm

    count = 0
    # Intermittent issue where charmcraft fails to build the charm for an unknown reason.
    # Retry building the charm
    while True:
        try:
            subprocess.check_call(["charmcraft", "pack", "-v"])
            return Path("./tempo-coordinator-k8s_ubuntu-22.04-amd64.charm").absolute()
        except subprocess.CalledProcessError:
            logger.warning("Failed to build Tempo coordinator. Trying again!")
            count += 1

            if count == 3:
                raise


@fixture(scope="module", autouse=True)
def copy_charm_libs_into_tester_charm(ops_test):
    """Ensure the tester charm has the libraries it uses."""
    libraries = [
        "observability_libs/v1/cert_handler.py",
        "tls_certificates_interface/v3/tls_certificates.py",
        "tempo_coordinator_k8s/v0/charm_tracing.py",
        "tempo_coordinator_k8s/v0/tracing.py",
    ]

    copies = []

    for lib in libraries:
        install_path = f"tests/integration/tester/lib/charms/{lib}"
        os.makedirs(os.path.dirname(install_path), exist_ok=True)
        shutil.copyfile(f"lib/charms/{lib}", install_path)
        copies.append(install_path)

    yield

    # cleanup: remove all libs
    check_output(shlex.split("rm -rf ./tests/integration/tester/lib"))


@fixture(scope="module", autouse=True)
def copy_charm_libs_into_tester_grpc_charm(ops_test):
    """Ensure the tester GRPC charm has the libraries it uses."""
    libraries = [
        "tempo_coordinator_k8s/v0/tracing.py",
    ]

    copies = []

    for lib in libraries:
        install_path = f"tests/integration/tester-grpc/lib/charms/{lib}"
        os.makedirs(os.path.dirname(install_path), exist_ok=True)
        shutil.copyfile(f"lib/charms/{lib}", install_path)
        copies.append(install_path)

    yield

    # cleanup: remove all libs
    check_output(shlex.split("rm -rf ./tests/integration/tester-grpc/lib"))


@fixture(scope="function")
def server_cert(ops_test: OpsTest):
    data = get_relation_data(
        requirer_endpoint=f"{APP_NAME}/0:certificates",
        provider_endpoint=f"{SSC_APP_NAME}/0:certificates",
        model=ops_test.model.name,
    )
    cert = json.loads(data.provider.application_data["certificates"])[0]["certificate"]

    with tempfile.NamedTemporaryFile() as f:
        p = Path(f.name)
        p.write_text(cert)
        yield p


@fixture(scope="function")
def nonce():
    """Generate an integer nonce for easier trace querying."""
    return str(random.random())[2:]
