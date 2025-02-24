import json
from dataclasses import replace

import pytest
import yaml
from cosl.coordinated_workers.interface import ClusterProviderAppData
from ops.testing import Relation
from scenario import State

from charm import TempoCoordinatorCharm
from tempo import TempoConfigBuilderDefault


def test_memberlist_multiple_members(
    context, all_worker, s3, nginx_container, nginx_prometheus_exporter_container
):
    workers_no = 3
    all_worker = replace(
        all_worker,
        remote_units_data={
            worker_idx: {
                "address": json.dumps(f"worker-{worker_idx}.test.svc.cluster.local:7946"),
                "juju_topology": json.dumps(
                    {
                        "model": "test",
                        "unit": f"worker/{worker_idx}",
                        "model_uuid": "1",
                        "application": "worker",
                        "charm_name": "TempoWorker",
                    }
                ),
            }
            for worker_idx in range(workers_no)
        },
    )
    state = State(
        leader=True,
        relations=[all_worker, s3],
        containers=[nginx_container, nginx_prometheus_exporter_container],
    )
    with context(context.on.relation_changed(all_worker), state) as mgr:
        charm: TempoCoordinatorCharm = mgr.charm
        assert charm.coordinator.cluster.gather_addresses() == tuple(
            [
                "worker-0.test.svc.cluster.local:7946",
                "worker-1.test.svc.cluster.local:7946",
                "worker-2.test.svc.cluster.local:7946",
            ]
        )


def test_metrics_generator(
    context,
    all_worker,
    s3,
    nginx_container,
    nginx_prometheus_exporter_container,
    remote_write,
):
    state = State(
        leader=True,
        relations=[all_worker, s3],
        containers=[nginx_container, nginx_prometheus_exporter_container],
    )
    with context(context.on.relation_changed(all_worker), state) as mgr:
        charm: TempoCoordinatorCharm = mgr.charm
        config_raw = TempoConfigBuilderDefault(charm.tempo).build(charm.coordinator)
        config = yaml.safe_load(config_raw)
        assert "metrics_generator" not in config
        assert "overrides" not in config

    # add remote-write relation
    state = State(
        leader=True,
        relations=[all_worker, s3, remote_write],
        containers=[nginx_container, nginx_prometheus_exporter_container],
    )

    with context(context.on.relation_changed(remote_write), state) as mgr:
        charm: TempoCoordinatorCharm = mgr.charm
        # assert charm.coordinator.cert_handler.server_cert
        config_raw = TempoConfigBuilderDefault(charm.tempo).build(charm.coordinator)
        config = yaml.safe_load(config_raw)
        assert "metrics_generator" in config
        assert config["metrics_generator"]["storage"]["remote_write"] == [
            json.loads(remote_write.remote_units_data[0]["remote_write"])
        ]
        assert "overrides" in config


@pytest.mark.parametrize(
    "worker_version, is_default, is_empty",
    (
        ("2.6", True, False),
        ("2.7.1", False, False),
        (None, True, False),
        ("2.8", False, True),
    ),
)
def test_multi_config_generation(
    context,
    all_worker,
    s3,
    nginx_container,
    nginx_prometheus_exporter_container,
    worker_version,
    is_default,
    is_empty,
):
    # GIVEN worker requests for a specific version
    worker = Relation(
        "tempo-cluster",
        remote_app_data={
            "role": '"all"',
            "workload_version": json.dumps(worker_version),
        },
        remote_units_data={
            0: {
                "address": json.dumps("localhost"),
                "juju_topology": json.dumps(
                    {"application": "worker", "unit": "worker/0", "charm_name": "tempo"}
                ),
            }
        },
    )

    state = State(
        leader=True,
        relations=[worker, s3],
        containers=[nginx_container, nginx_prometheus_exporter_container],
    )

    # WHEN any event is fired
    with context(context.on.relation_changed(worker), state) as mgr:
        state_out = mgr.run()
        cluster_out = state_out.get_relations(worker.endpoint)[0]
        local_app_data = ClusterProviderAppData.load(cluster_out.local_app_data)
        # THEN the correct config has been generated
        if is_empty:
            assert local_app_data.worker_config == ""
        if is_default:
            assert "use_otel_tracer" in local_app_data.worker_config
        else:
            assert "use_otel_tracer" not in local_app_data.worker_config
