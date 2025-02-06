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

from pytest import fixture
from pytest_operator.plugin import OpsTest

from tests.integration.helpers import get_relation_data

APP_NAME = "tempo"
SSC = "self-signed-certificates"
SSC_APP_NAME = "ssc"

logger = logging.getLogger(__name__)


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


@fixture(scope="module")
async def copy_tempo_libraries_into_tempo_api_requirer_tester_charm(ops_test: OpsTest):
    charm_path = (Path(__file__).parent / "tempo-api-tester").absolute()

    # Update libraries in the tester charms
    root_lib_folder = Path(__file__).parent.parent.parent / "lib"
    tester_lib_folder = charm_path / "lib"

    if os.path.exists(tester_lib_folder):
        shutil.rmtree(tester_lib_folder)
    shutil.copytree(root_lib_folder, tester_lib_folder)


@fixture(scope="module")
async def tempo_api_requirer_tester_charm(
    ops_test: OpsTest, copy_tempo_libraries_into_tempo_api_requirer_tester_charm
) -> Path:
    """A charm to integration test the tempo-api relation."""
    charm_path = "tests/integration/tempo-api-tester"
    charm = await ops_test.build_charm(charm_path)
    return charm


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
