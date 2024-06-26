from unittest.mock import patch

import pytest
from scenario import Context, Relation

from charm import TempoCoordinatorCharm
from tempo_cluster import TempoClusterRequirerAppData, TempoRole


@pytest.fixture
def tempo_charm():
    with patch("charm.KubernetesServicePatch"):
        with patch("lightkube.core.client.GenericSyncClient"):
            with patch("charm.TempoCoordinatorCharm._update_server_cert"):
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
        remote_app_data=TempoClusterRequirerAppData(role=TempoRole.all).dump(),
    )
