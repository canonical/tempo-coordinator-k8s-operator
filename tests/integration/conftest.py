# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.
import json
import logging
import os
import random
import shlex
import shutil
import string
import subprocess
import tempfile
from pathlib import Path
from subprocess import check_output

import pytest
import yaml
from juju import Juju
from pytest import fixture

from tests.integration.helpers import get_relation_data

APP_NAME = "tempo"
TESTER_NAME = "tester"
TESTER_GRPC_NAME = "tester-grpc"
TRAEFIK = "traefik"
SSC = "self-signed-certificates"
SSC_APP_NAME = "ssc"

logger = logging.getLogger(__name__)


def pytest_addoption(parser):
    parser.addoption(
        "--charm-path",
        help="Pre-built charm file to deploy, rather than building from source",
    )
    parser.addoption(
        "--tester-charm-path",
        help="Pre-built charm file to deploy, rather than building from source",
    )
    parser.addoption(
        "--tester-grpc-charm-path",
        help="Pre-built charm file to deploy, rather than building from source",
    )


def _generate_random_model_name():
    name = "test-"
    for _ in range(15):
        name += random.choice(string.ascii_lowercase)
    return name


@fixture(scope="module", autouse=True)
def juju():
    model_name = _generate_random_model_name()
    unbound_juju = Juju()
    unbound_juju.cli("add-model", model_name, "--no-switch")
    yield Juju(model_name)
    unbound_juju.cli("destroy-model", model_name, "--destroy-storage", "true")


@fixture(scope="session", autouse=True)
def copy_charm_libs_into_tester_charm():
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


@fixture(scope="session", autouse=True)
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


@fixture(scope="session")
def tempo_charm(request):
    """Tempo charm used for integration testing."""
    if charm_file := request.config.getoption("--charm-path"):
        return charm_file

    subprocess.run(
        ["/snap/bin/charmcraft", "pack", "--verbose"],
        check=True,
        capture_output=True,
        text=True,
    )
    return next(Path.glob(Path("."), "*.charm")).absolute()


@fixture(scope="session")
def tester_charm(request):
    """Tempo charm used for integration testing."""
    if charm_file := request.config.getoption("--tester-path"):
        return charm_file
    path = "./tests/integration/tester/"
    subprocess.run(
        ["/snap/bin/charmcraft", "pack", "--verbose", "-p", path],
        check=True,
        capture_output=True,
        text=True,
    )
    return next(Path.glob(Path(path), "*.charm")).absolute()


@fixture(scope="session")
def tester_grpc_charm(request):
    """Tempo charm used for integration testing."""
    if charm_file := request.config.getoption("--tester-grpc-path"):
        return charm_file
    path = "./tests/integration/tester-grpc/"
    subprocess.run(
        ["/snap/bin/charmcraft", "pack", "--verbose", "-p", path],
        check=True,
        capture_output=True,
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
        Path("./tests/integration/tester-grpc/metadata.yaml").read_text(),
    )
    return {"workload": meta["resources"]["workload"]["upstream-source"]}


@fixture(scope="session")
def tempo_oci_image():
    meta = yaml.safe_load(Path("./charmcraft.yaml").read_text())
    return meta["resources"]["tempo-image"]["upstream-source"]


@fixture(scope="session")
def traefik_lb_ip(juju):
    proc = subprocess.run(
        [
            "/snap/bin/kubectl",
            "-n",
            juju.model_name(),
            "get",
            "service",
            f"{TRAEFIK}-lb",
            "-o=jsonpath='{.status.loadBalancer.ingress[0].ip}'",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    ip_address = proc.stdout.strip("'")
    return ip_address


@fixture(scope="function")
def server_cert(juju):
    data = get_relation_data(
        requirer_endpoint=f"{APP_NAME}/0:certificates",
        provider_endpoint=f"{SSC_APP_NAME}/0:certificates",
        model=juju.model_name(),
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
