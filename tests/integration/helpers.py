import json
import logging
import os
import shlex
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Literal, Optional, Sequence, Union

import jubilant
import requests
import yaml
from cosl.coordinated_workers.nginx import CA_CERT_PATH
from jubilant import Juju
from minio import Minio
from tenacity import retry, stop_after_attempt, wait_exponential

from tests.integration.conftest import _get_tempo_charm

_JUJU_DATA_CACHE = {}
_JUJU_KEYS = ("egress-subnets", "ingress-address", "private-address")
ACCESS_KEY = "accesskey"
SECRET_KEY = "secretkey"
MINIO = "minio"
BUCKET_NAME = "tempo"
S3_INTEGRATOR = "s3-integrator"
PROMETHEUS = "prometheus"
PROMETHEUS_CHARM = "prometheus-k8s"
WORKER_NAME = "tempo-worker"
TEMPO_APP = "tempo"
TRACEGEN_SCRIPT_PATH = Path() / "scripts" / "tracegen.py"
METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())

TEMPO_RESOURCES = {
    image_name: image_meta["upstream-source"] for image_name, image_meta in METADATA["resources"].items()
}

protocols_endpoints = {
    "jaeger_thrift_http": "{scheme}://{hostname}:14268/api/traces?format=jaeger.thrift",
    "zipkin": "{scheme}://{hostname}:9411/v1/traces",
    "jaeger_grpc": "{hostname}:14250",
    "otlp_http": "{scheme}://{hostname}:4318/v1/traces",
    "otlp_grpc": "{hostname}:4317",
}

api_endpoints = {
    "tempo_http": "{scheme}://{hostname}:3200/api",
    "tempo_grpc": "{hostname}:9096",
}

logger = logging.getLogger(__name__)


def purge(data: dict):
    for key in _JUJU_KEYS:
        if key in data:
            del data[key]


def get_unit_info(unit_name: str, model: str = None) -> dict:
    """Return unit-info data structure.

     for example:

    traefik-k8s/0:
      opened-ports: []
      charm: local:focal/traefik-k8s-1
      leader: true
      relation-info:
      - endpoint: ingress-per-unit
        related-endpoint: ingress
        application-data:
          _supported_versions: '- v1'
        related-units:
          prometheus-k8s/0:
            in-scope: true
            data:
              egress-subnets: 10.152.183.150/32
              ingress-address: 10.152.183.150
              private-address: 10.152.183.150
      provider-id: traefik-k8s-0
      address: 10.1.232.144
    """
    cmd = f"juju show-unit {unit_name}".split(" ")
    if model:
        cmd.insert(2, "-m")
        cmd.insert(3, model)

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    raw_data = proc.stdout.read().decode("utf-8").strip()

    data = yaml.safe_load(raw_data) if raw_data else None

    if not data:
        raise ValueError(
            f"no unit info could be grabbed for {unit_name}; "
            f"are you sure it's a valid unit name?"
            f"cmd={' '.join(proc.args)}"
        )

    if unit_name not in data:
        raise KeyError(unit_name, f"not in {data!r}")

    unit_data = data[unit_name]
    _JUJU_DATA_CACHE[unit_name] = unit_data
    return unit_data


def get_relation_by_endpoint(relations, local_endpoint, remote_endpoint, remote_obj):
    matches = [
        r
        for r in relations
        if (
                   (r["endpoint"] == local_endpoint and r["related-endpoint"] == remote_endpoint)
                   or (r["endpoint"] == remote_endpoint and r["related-endpoint"] == local_endpoint)
           )
           and remote_obj in r["related-units"]
    ]
    if not matches:
        raise ValueError(
            f"no matches found with endpoint=="
            f"{local_endpoint} "
            f"in {remote_obj} (matches={matches})"
        )
    if len(matches) > 1:
        raise ValueError(
            "multiple matches found with endpoint=="
            f"{local_endpoint} "
            f"in {remote_obj} (matches={matches})"
        )
    return matches[0]


@dataclass
class UnitRelationData:
    unit_name: str
    endpoint: str
    leader: bool
    application_data: Dict[str, str]
    unit_data: Dict[str, str]


def get_content(
        obj: str, other_obj, include_default_juju_keys: bool = False, model: str = None
) -> UnitRelationData:
    """Get the content of the databag of `obj`, as seen from `other_obj`."""
    unit_name, endpoint = obj.split(":")
    other_unit_name, other_endpoint = other_obj.split(":")

    unit_data, app_data, leader = get_databags(
        unit_name, endpoint, other_unit_name, other_endpoint, model
    )

    if not include_default_juju_keys:
        purge(unit_data)

    return UnitRelationData(unit_name, endpoint, leader, app_data, unit_data)


def get_databags(local_unit, local_endpoint, remote_unit, remote_endpoint, model):
    """Get the databags of local unit and its leadership status.

    Given a remote unit and the remote endpoint name.
    """
    local_data = get_unit_info(local_unit, model)
    leader = local_data["leader"]

    data = get_unit_info(remote_unit, model)
    relation_info = data.get("relation-info")
    if not relation_info:
        raise RuntimeError(f"{remote_unit} has no relations")

    raw_data = get_relation_by_endpoint(relation_info, local_endpoint, remote_endpoint, local_unit)
    unit_data = raw_data["related-units"][local_unit]["data"]
    app_data = raw_data["application-data"]
    return unit_data, app_data, leader


@dataclass
class RelationData:
    provider: UnitRelationData
    requirer: UnitRelationData


def get_relation_data(
        *,
        provider_endpoint: str,
        requirer_endpoint: str,
        include_default_juju_keys: bool = False,
        model: str = None,
):
    """Get relation databags for a juju relation.

    >>> get_relation_data('prometheus/0:ingress', 'traefik/1:ingress-per-unit')
    """
    provider_data = get_content(
        provider_endpoint, requirer_endpoint, include_default_juju_keys, model
    )
    requirer_data = get_content(
        requirer_endpoint, provider_endpoint, include_default_juju_keys, model
    )
    return RelationData(provider=provider_data, requirer=requirer_data)


def run_command(model_name: str, app_name: str, unit_num: int, command: list) -> bytes:
    cmd = ["juju", "ssh", "--model", model_name, f"{app_name}/{unit_num}", *command]
    try:
        res = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        logger.info(res)
    except subprocess.CalledProcessError as e:
        logger.error(e.stdout.decode())
        raise e
    return res.stdout


def present_facade(
        interface: str,
        app_data: Dict = None,
        unit_data: Dict = None,
        role: Literal["provide", "require"] = "provide",
        model: str = None,
        app: str = "facade",
):
    """Set up the facade charm to present this data over the interface ``interface``."""
    data = {
        "endpoint": f"{role}-{interface}",
    }
    if app_data:
        data["app_data"] = json.dumps(app_data)
    if unit_data:
        data["unit_data"] = json.dumps(unit_data)

    with tempfile.NamedTemporaryFile(dir=os.getcwd()) as f:
        fpath = Path(f.name)
        fpath.write_text(yaml.safe_dump(data))

        _model = f" --model {model}" if model else ""

        subprocess.run(shlex.split(f"juju run {app}/0{_model} update --params {fpath.absolute()}"))


def get_app_ip_address(juju: Juju, app_name):
    """Return a juju application's IP address."""
    return juju.status().apps[app_name].address


def get_unit_ip_address(juju: Juju, app_name: str, unit_no: int):
    """Return a juju unit's IP address."""
    return juju.status().apps[app_name].units[f"{app_name}/{unit_no}"].public_address


def _deploy_and_configure_minio(juju: Juju):
    config = {
        "access-key": ACCESS_KEY,
        "secret-key": SECRET_KEY,
    }
    juju.deploy(MINIO, channel="edge", trust=True, config=config)
    juju.wait(
        lambda status: status.apps[MINIO].is_active,
        error=jubilant.any_error,
    )
    minio_addr = get_unit_ip_address(juju, MINIO, 0)

    mc_client = Minio(
        f"{minio_addr}:9000",
        access_key="accesskey",
        secret_key="secretkey",
        secure=False,
    )

    # create tempo bucket
    found = mc_client.bucket_exists(BUCKET_NAME)
    if not found:
        mc_client.make_bucket(BUCKET_NAME)

    # configure s3-integrator
    config = {
        "endpoint": f"minio-0.minio-endpoints.{juju.model}.svc.cluster.local:9000",
        "bucket": BUCKET_NAME,
    }
    juju.config(S3_INTEGRATOR, config)
    task = juju.run(S3_INTEGRATOR + "/0", "sync-s3-credentials", params=config)
    assert task.status == "completed"


def tempo_worker_charm_and_channel_and_resources():
    """Tempo worker charm used for integration testing.

    Build once per session and reuse it in all integration tests to save some minutes/hours.
    You can also set `WORKER_CHARM_PATH` env variable to use an already existing built charm.
    """
    if path_from_env := os.getenv("WORKER_CHARM_PATH"):
        worker_charm_path = Path(path_from_env).absolute()
        logger.info("Using local tempo worker charm: %s", worker_charm_path)
        return (
            worker_charm_path,
            None,
            get_resources(worker_charm_path.parent),
        )
    return "tempo-worker-k8s", "edge", None


def get_resources(path: Union[str, Path]):
    meta = yaml.safe_load((Path(path) / "charmcraft.yaml").read_text())
    resources_meta = meta.get("resources", {})
    return {res_name: res_meta["upstream-source"] for res_name, res_meta in resources_meta.items()}


def _deploy_cluster(juju: Juju, workers: Sequence[str], tempo_deployed_as: str = None):
    if tempo_deployed_as:
        tempo_app = tempo_deployed_as
    else:
        juju.deploy(
            _get_tempo_charm(), TEMPO_APP, resources=TEMPO_RESOURCES, trust=True
        )
        tempo_app = TEMPO_APP

    juju.deploy(S3_INTEGRATOR, channel="edge")

    juju.integrate(tempo_app + ":s3", S3_INTEGRATOR + ":s3-credentials")
    for worker in workers:
        juju.integrate(tempo_app + ":tempo-cluster", worker + ":tempo-cluster")

    _deploy_and_configure_minio(juju)

    juju.wait(
        lambda status: jubilant.all_active(status, [tempo_app, *workers, S3_INTEGRATOR]),
        timeout=2000,
    )


def deploy_monolithic_cluster(juju: Juju, tempo_deployed_as=None):
    """Deploy a tempo-monolithic cluster.

    `param:tempo_app`: tempo-coordinator is already deployed as this app.
    """
    tempo_worker_charm_url, channel, resources = tempo_worker_charm_and_channel_and_resources()
    juju.deploy(
        tempo_worker_charm_url,
        app=WORKER_NAME,
        channel=channel,
        trust=True,
        resources=resources,
    )
    _deploy_cluster(juju, [WORKER_NAME], tempo_deployed_as=tempo_deployed_as)


def deploy_distributed_cluster(juju: Juju, roles: Sequence[str], tempo_deployed_as=None):
    """This assumes tempo-coordinator is already deployed as `param:tempo_app`."""
    tempo_worker_charm_url, channel, resources = tempo_worker_charm_and_channel_and_resources()

    all_workers = []

    for role in roles:
        worker_name = f"{WORKER_NAME}-{role}"
        all_workers.append(worker_name)

        juju.deploy(
            tempo_worker_charm_url,
            app=worker_name,
            channel=channel,
            trust=True,
            config={"role-all": False, f"role-{role}": True},
            resources=resources,
        )

    _deploy_cluster(juju, all_workers, tempo_deployed_as=tempo_deployed_as)


def get_traces(tempo_host: str, service_name="tracegen", tls=True):
    url = f"{'https' if tls else 'http'}://{tempo_host}:3200/api/search?tags=service.name={service_name}"
    req = requests.get(
        url,
        verify=False,
    )
    assert req.status_code == 200
    traces = json.loads(req.text)["traces"]
    return traces


@retry(stop=stop_after_attempt(15), wait=wait_exponential(multiplier=1, min=4, max=10))
def get_traces_patiently(tempo_host, service_name="tracegen", tls=True):
    logger.info(f"polling {tempo_host} for service {service_name!r} traces...")
    traces = get_traces(tempo_host, service_name=service_name, tls=tls)
    assert len(traces) > 0
    return traces


def emit_trace(
        endpoint,
        juju: Juju,
        nonce: str = None,
        proto: str = "otlp_http",
        service_name: Optional[str] = "tracegen",
        verbose=0,
        use_cert=False,
):
    """Use juju ssh to run tracegen from the tempo charm; to avoid any DNS issues."""
    # SCP tracegen script onto unit and install dependencies
    logger.info(f"pushing tracegen onto {TEMPO_APP}/0")

    juju.cli("scp", str(TRACEGEN_SCRIPT_PATH), f"{TEMPO_APP}/0:tracegen.py")
    juju.cli(
        "ssh",
        f"{TEMPO_APP}/0",
        "python3 -m pip install protobuf==3.20.* opentelemetry-exporter-otlp-proto-grpc opentelemetry-exporter-otlp-proto-http"
        + " opentelemetry-exporter-zipkin opentelemetry-exporter-jaeger",
    )

    cmd = (
        f"juju ssh -m {juju.model} {TEMPO_APP}/0 "
        f"TRACEGEN_SERVICE={service_name or ''} "
        f"TRACEGEN_ENDPOINT={endpoint} "
        f"TRACEGEN_VERBOSE={verbose} "
        f"TRACEGEN_PROTOCOL={proto} "
        f"TRACEGEN_CERT={CA_CERT_PATH if use_cert else ''} "
        f"TRACEGEN_NONCE={nonce or ''} "
        "python3 tracegen.py"
    )

    logger.info(f"running tracegen with {cmd!r}")

    out = subprocess.run(shlex.split(cmd), text=True, capture_output=True).stdout
    logger.info(f"tracegen completed; stdout={out!r}")


def _get_endpoint(protocol: str, hostname: str, tls: bool):
    protocol_endpoint = protocols_endpoints.get(protocol) or api_endpoints.get(protocol)
    if protocol_endpoint is None:
        assert False, f"Invalid {protocol}"

    if "grpc" in protocol:
        # no scheme in _grpc endpoints
        return protocol_endpoint.format(hostname=hostname)
    else:
        return protocol_endpoint.format(hostname=hostname, scheme="https" if tls else "http")


def get_tempo_ingressed_endpoint(hostname, protocol: str, tls: bool):
    return _get_endpoint(protocol, hostname, tls)


def get_tempo_internal_endpoint(juju: Juju, protocol: str, tls: bool):
    hostname = f"{TEMPO_APP}-0.{TEMPO_APP}-endpoints.{juju.model}.svc.cluster.local"
    return _get_endpoint(protocol, hostname, tls)


def get_tempo_application_endpoint(tempo_ip: str, protocol: str, tls: bool):
    return _get_endpoint(protocol, tempo_ip, tls)
