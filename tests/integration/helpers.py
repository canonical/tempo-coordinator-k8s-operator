import json
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

import requests
import yaml
from minio import Minio
from tenacity import retry, stop_after_attempt, wait_exponential

from tempo import Tempo
from tests.integration.juju import WorkloadStatus

_JUJU_DATA_CACHE = {}
_JUJU_KEYS = ("egress-subnets", "ingress-address", "private-address")
ACCESS_KEY = "accesskey"
SECRET_KEY = "secretkey"
MINIO = "minio"
BUCKET_NAME = "tempo"
S3_INTEGRATOR = "s3-integrator"
WORKER_NAME = "tempo-worker"
protocols_endpoints = {
    "jaeger_thrift_http": "https://{}:14268/api/traces?format=jaeger.thrift",
    "zipkin": "https://{}:9411/v1/traces",
    "jaeger_grpc": "{}:14250",
    "otlp_http": "https://{}:4318/v1/traces",
    "otlp_grpc": "{}:4317",
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
            (
                r["endpoint"] == local_endpoint
                and r["related-endpoint"] == remote_endpoint
            )
            or (
                r["endpoint"] == remote_endpoint
                and r["related-endpoint"] == local_endpoint
            )
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

    raw_data = get_relation_by_endpoint(
        relation_info, local_endpoint, remote_endpoint, local_unit
    )
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


def get_unit_address(juju, app_name, unit_no):
    status = juju.status()
    app = status["applications"][app_name]
    if app is None:
        assert False, f"no app exists with name {app_name}"
    unit = app["units"].get(f"{app_name}/{unit_no}")
    if unit is None:
        assert False, f"no unit exists in app {app_name} with index {unit_no}"
    try:
        return unit["address"]
    except:
        logger.exception(json.dumps(unit, indent=2))
        raise


def deploy_and_configure_minio(juju):
    config = {
        "access-key": ACCESS_KEY,
        "secret-key": SECRET_KEY,
    }
    juju.deploy(MINIO, channel="edge", trust=True, config=config)
    juju.wait(
        stop=lambda status: status.all_workloads((MINIO,), WorkloadStatus.active),
        timeout=2000,
        refresh_rate=5,
    )

    minio_addr = get_unit_address(juju, MINIO, "0")

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
    juju.config(
        S3_INTEGRATOR,
        {
            "endpoint": f"minio-0.minio-endpoints.{juju.model_name()}.svc.cluster.local:9000",
            "bucket": BUCKET_NAME,
        },
    )

    action_result = juju.run(S3_INTEGRATOR, "sync-s3-credentials", params=config)
    assert action_result["status"] == "completed"


def tempo_worker_charm_and_channel():
    """Tempo worker charm used for integration testing.

    Build once per session and reuse it in all integration tests to save some minutes/hours.
    You can also set `TEMPO_WORKER_CHARM` env variable to use an already existing built charm.
    """
    if path_from_env := os.getenv("TEMPO_WORKER_CHARM"):
        return Path(path_from_env).absolute(), None
    return "tempo-worker-k8s", "edge"


APP_NAME = "tempo"


def deploy_cluster(juju, tempo_app=APP_NAME):
    tempo_worker_charm_url, channel = tempo_worker_charm_and_channel()
    juju.deploy(tempo_worker_charm_url, alias=WORKER_NAME, channel=channel, trust=True)
    juju.deploy(S3_INTEGRATOR, channel="edge")

    juju.integrate(tempo_app + ":s3", S3_INTEGRATOR + ":s3-credentials")
    juju.integrate(tempo_app + ":tempo-cluster", WORKER_NAME + ":tempo-cluster")

    deploy_and_configure_minio(juju)
    juju.wait(
        stop=lambda status: status.all_workloads(
            (tempo_app, WORKER_NAME, S3_INTEGRATOR), WorkloadStatus.active
        ),
        timeout=2000,
    )


def get_traces(tempo_host: str, service_name="tracegen-otlp_http", tls=True):
    url = f"{'https' if tls else 'http'}://{tempo_host}:3200/api/search?tags=service.name={service_name}"
    req = requests.get(
        url,
        verify=False,
    )
    assert req.status_code == 200
    traces = json.loads(req.text)["traces"]
    return traces


@retry(stop=stop_after_attempt(15), wait=wait_exponential(multiplier=1, min=4, max=10))
def get_traces_patiently(tempo_host, service_name="tracegen-otlp_http", tls=True):
    traces = get_traces(tempo_host, service_name=service_name, tls=tls)
    assert len(traces) > 0
    return traces


def emit_trace(
    endpoint, juju, nonce, proto: str = "otlp_http", verbose=0, use_cert=False
):
    """Use juju ssh to run tracegen from the tempo charm; to avoid any DNS issues."""
    cmd = (
        f"juju ssh -m {juju.model_name()} {APP_NAME}/0 "
        f"TRACEGEN_ENDPOINT={endpoint} "
        f"TRACEGEN_VERBOSE={verbose} "
        f"TRACEGEN_PROTOCOL={proto} "
        f"TRACEGEN_CERT={Tempo.tls_ca_path if use_cert else ''} "
        f"TRACEGEN_NONCE={nonce} "
        "python3 tracegen.py"
    )
    return subprocess.getoutput(cmd)


TESTER_NAME = "tester"
TESTER_GRPC_NAME = "tester-grpc"
TRAEFIK = "traefik"
SSC = "self-signed-certificates"
SSC_APP_NAME = "ssc"
