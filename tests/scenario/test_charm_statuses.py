from unittest.mock import patch

import ops

from scenario import PeerRelation, State

from tempo import Tempo


def test_monolithic_status_no_s3_no_workers(context):
    state_out = context.run("start", State(unit_status=ops.ActiveStatus()))
    assert state_out.unit_status.name == "blocked"


def test_scaled_status_no_s3(context, all_worker):
    state_out = context.run(
        "start",
        State(
            relations=[PeerRelation("peers", peers_data={1: {}, 2: {}})],
            unit_status=ops.ActiveStatus(),
        ),
    )
    assert state_out.unit_status.name == "blocked"


def test_scaled_status_no_workers(context, all_worker):
    state_out = context.run(
        "start",
        State(
            relations=[PeerRelation("peers", peers_data={1: {}, 2: {}})],
            unit_status=ops.ActiveStatus(),
        ),
    )
    assert state_out.unit_status.name == "blocked"


def test_scaled_status_with_s3_and_workers(context, s3, all_worker, nginx_container, nginx_prometheus_exporter_container):
    state_out = context.run(
        "start",
        State(
            relations=[PeerRelation("peers", peers_data={1: {}, 2: {}}), s3, all_worker],
            containers=[nginx_container, nginx_prometheus_exporter_container],
            unit_status=ops.ActiveStatus(),
        ),
    )
    assert state_out.unit_status.name == "active"


@patch.object(Tempo, "is_ready", new=True)
def test_happy_status(context, s3, all_worker, nginx_container, nginx_prometheus_exporter_container):
    state_out = context.run(
        "start",
        State(
            relations=[PeerRelation("peers", peers_data={1: {}, 2: {}}), s3, all_worker],
            containers=[nginx_container, nginx_prometheus_exporter_container],
            unit_status=ops.ActiveStatus(),
        ),
    )
    assert state_out.unit_status.name == "active"
