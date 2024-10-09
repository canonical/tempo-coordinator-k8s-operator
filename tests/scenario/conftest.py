import json
from unittest.mock import MagicMock, patch

import pytest
from charms.tempo_coordinator_k8s.v0.charm_tracing import charm_tracing_disabled
from ops import ActiveStatus
from scenario import Container, Context, Relation

from charm import TempoCoordinatorCharm


@pytest.fixture(autouse=True)
def patch_buffer_file_for_charm_tracing(tmp_path):
    with patch(
        "charms.tempo_coordinator_k8s.v0.charm_tracing.BUFFER_DEFAULT_CACHE_FILE_NAME",
        str(tmp_path / "foo.json"),
    ):
        yield


@pytest.fixture(autouse=True, scope="session")
def disable_charm_tracing():
    with charm_tracing_disabled():
        yield


@pytest.fixture()
def coordinator():
    return MagicMock()


@pytest.fixture
def tempo_charm(tmp_path):
    with patch("lightkube.core.client.GenericSyncClient"):
        with patch("charm.TempoCoordinatorCharm.are_certificates_on_disk", False):
            with patch("tempo.Tempo.tls_ca_path", str(tmp_path / "cert.tmp")):
                with patch.multiple(
                    "cosl.coordinated_workers.coordinator.KubernetesComputeResourcesPatch",
                    _namespace="test-namespace",
                    _patch=lambda _: None,
                    get_status=lambda _: ActiveStatus(""),
                    is_ready=lambda _: True,
                ):
                    yield TempoCoordinatorCharm


@pytest.fixture(scope="function")
def context(tempo_charm):
    return Context(charm_type=tempo_charm)


@pytest.fixture(scope="function")
def s3_config():
    return {
        "access-key": "key",
        "bucket": "tempo",
        "endpoint": "http://1.2.3.4:9000",
        "secret-key": "soverysecret",
    }


@pytest.fixture(scope="function")
def s3(s3_config):
    return Relation(
        "s3",
        remote_app_data=s3_config,
        local_unit_data={"bucket": "tempo"},
    )


@pytest.fixture(scope="function")
def all_worker():
    return Relation(
        "tempo-cluster",
        remote_app_data={"role": '"all"'},
    )


@pytest.fixture(scope="function")
def remote_write():
    return Relation(
        "send-remote-write",
        remote_units_data={
            0: {"remote_write": json.dumps({"url": "http://prometheus:3000/api/write"})}
        },
    )


@pytest.fixture(scope="function")
def nginx_container():
    return Container(
        "nginx",
        can_connect=True,
    )


@pytest.fixture(scope="function")
def nginx_prometheus_exporter_container():
    return Container(
        "nginx-prometheus-exporter",
        can_connect=True,
    )
