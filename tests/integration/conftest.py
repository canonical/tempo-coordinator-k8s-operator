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
from json import JSONDecodeError
from pathlib import Path
from subprocess import CalledProcessError, check_output

import pytest
import yaml
from juju import Juju, generate_random_model_name
from pytest import fixture

from tests.integration.helpers import APP_NAME, SSC_APP_NAME, TRAEFIK, get_relation_data
from tests.integration.juju import JujuLogLevel

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).parent.parent.parent.absolute()


def pytest_configure(config):
    # register your new marker to avoid warnings
    config.addinivalue_line("markers", "setup: setup tests")
    config.addinivalue_line("markers", "teardown: teardown tests")


def pytest_collection_modifyitems(config, items):
    for option, mark in (("--no-setup", "setup"), ("--no-teardown", "teardown")):
        if config.getoption(option):
            skip = pytest.mark.skip(reason=f"{option} provided")
            for item in items:
                if mark in item.keywords:
                    # collect all items that have a key marker with that value
                    item.add_marker(skip)


def pytest_addoption(parser):
    parser.addoption(
        "--dump-jdl",
        action="store",
        help="Folder in which to save the juju debug-logs when the tests are done.",
    )
    parser.addoption(
        "--no-setup",
        action="store_true",
        default=False,
        help="Only execute tests marked as 'startup'.",
    )
    parser.addoption(
        "--no-teardown",
        action="store_true",
        default=False,
        help="Only execute tests marked as 'teardown'.",
    )
    parser.addoption(
        "--switch",
        action="store_true",
        default=False,
        help="Switch to the model you're testing in.",
    )
    parser.addoption(
        "--nuke",
        help="--force teardown the model when done.",
    )
    parser.addoption(
        "--model",
        help="Use this model name instead of a randomly generated one. Implies --keep-models.",
    )
    parser.addoption(
        "--keep-models",
        action="store_true",
        default=False,
        help="Do not destroy the models on exit.",
    )
    parser.addoption(
        "--tempo-coordinator-k8s-charm-path",
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


@fixture(scope="module", autouse=True)
def juju(request) -> Juju:
    model_name = request.config.getoption("--model") or generate_random_model_name()
    unbound_juju = Juju()
    try:
        Juju(model_name).status(quiet=True)
    except CalledProcessError:
        unbound_juju.add_model(model_name, switch=False)

    juju = Juju(model_name)
    if request.config.getoption("--switch"):
        juju.switch()

    try:
        yield juju
    finally:
        logger.info(f"==== captured juju debug-log for model {juju.model_name()} =====")
        model_jdl = juju.debug_log(replay=True, level=JujuLogLevel.DEBUG)
        if jdl_dir := request.config.getoption("--dump-jdl"):
            try:
                jdl_dir = Path(jdl_dir)
                jdl_dir.mkdir(parents=True, exist_ok=True)
                jdl_path = jdl_dir / juju.model_name()
                jdl_path.write_text(model_jdl)
                logger.info(f"jdl written to {jdl_path.resolve().absolute()}")
            except Exception:
                logger.exception(f"failed writing jdl to {jdl_dir}")
                print(model_jdl)
        else:
            print(model_jdl)

        should_teardown_model = True
        if request.config.getoption("--keep-models"):
            should_teardown_model = False
        elif request.config.getoption("--model"):
            should_teardown_model = False
        elif request.config.getoption("--no-teardown"):
            should_teardown_model = False
        if should_teardown_model:
            juju.destroy_model(
                destroy_storage=True, force=request.config.getoption("--nuke")
            )
        else:
            logger.info("skipping model teardown")


@fixture(scope="session", autouse=True)
def generate_requirements_files():
    """Ensure we have our dependencies ready in case we need to pack things."""
    check_output(shlex.split("make generate-requirements"))


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
def copy_charm_libs_into_tester_grpc_charm():
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


def _get_packed_charm(
    request,
    name: str,
    charm_path: str,
    request_option_flag: str = None,
):
    logger.info("checking if it was passed as a pytest option...")
    # check if user passed it as a config option to pytest
    option = request_option_flag or f"--{name}-charm-path"
    if charm_file := request.config.getoption(option):
        logger.info(
            f"success: path to charm {name!r} was passed as pytest option {option}."
        )
        return charm_file

    # check if packed file exists in charm_root_path folder
    logger.info(f"checking if a local file {charm_path!r} already exists...")
    if (fpath := PROJECT_ROOT / charm_path).exists():
        logger.info(f"success: local charm {name!r} was found at {fpath}.")
        return fpath.name


def _pack_charm(
    name: str,
    charm_path: str,
    charm_root_path: str = None,
):
    charm_root = charm_root_path or f"./tests/integration/{name}/"
    logger.info(f"local charm {name!r} not found. Packing project {charm_root!r} ...")
    proc = subprocess.run(
        [
            "/snap/bin/charmcraft",
            "pack",
            "--verbose",
            "-p",
            charm_root,
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        # TODO: check that this is indeed the right format, can't test
        #  because of https://github.com/canonical/charmcraft/issues/1985
        return list(json.loads(proc.stdout).keys())[0]
    except JSONDecodeError:
        hack = charm_path
        logger.error(
            f"`charmcraft pack` has produced useless output; "
            f"cfr. https://github.com/canonical/charmcraft/issues/1985 "
            f"This hardcoded value will be used instead: {hack!r}"
        )
        return hack


def get_charm(
    request,
    name: str,
    charm_root_path: str = None,
    packed_charm_filename: str = None,
    request_option_flag: str = None,
) -> str:
    """Obtain a charm, either from a local filename or by running charmcraft."""
    logger.info(f"getting charm {name!r}...")
    charm_path = packed_charm_filename or f"{name}_ubuntu-22.04-amd64.charm"

    if cached_charm := _get_packed_charm(
        request,
        name,
        charm_path,
        request_option_flag,
    ):
        return f"./{cached_charm}"

    # last resort: pack it
    packed_charm = _pack_charm(name, charm_path, charm_root_path)
    return f"./{packed_charm}"


@fixture(scope="session")
def tempo_charm(request):
    """Tempo charm used for integration testing."""
    return get_charm(request, "tempo-coordinator-k8s", charm_root_path="./")


@fixture(scope="session")
def tester_charm(request):
    """Tempo charm used for integration testing."""
    return get_charm(request, "tester")


@fixture(scope="session")
def tester_grpc_charm(request):
    """Tempo charm used for integration testing."""
    return get_charm(request, "tester-grpc")


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
