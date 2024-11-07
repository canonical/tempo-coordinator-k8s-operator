import socket
from dataclasses import replace

import pytest
from charms.tempo_coordinator_k8s.v0.charm_tracing import charm_tracing_disabled
from charms.tempo_coordinator_k8s.v0.tracing import (
    ProtocolType,
    Receiver,
    TracingProviderAppData,
)
from scenario import Relation, State


@pytest.fixture
def base_state(nginx_container, nginx_prometheus_exporter_container):
    return State(
        leader=True,
        containers=[nginx_container, nginx_prometheus_exporter_container],
    )


@pytest.mark.parametrize("evt_name", ("changed", "created", "joined"))
def test_tracing_v2_endpoint_published(context, s3, all_worker, evt_name, base_state):
    tracing = Relation("tracing", remote_app_data={"receivers": "[]"})
    state = replace(base_state, relations=[tracing, s3, all_worker])

    with charm_tracing_disabled():
        with context(getattr(context.on, f"relation_{evt_name}")(tracing), state) as mgr:
            assert len(mgr.charm._requested_receivers()) == 2
            out = mgr.run()

    tracing_out = out.get_relations(tracing.endpoint)[0]
    expected_data = TracingProviderAppData(
        receivers=[
            Receiver(
                protocol=ProtocolType(name="otlp_http", type="http"),
                url=f"http://{socket.getfqdn()}:4318",
            ),
            Receiver(
                protocol=ProtocolType(name="jaeger_thrift_http", type="http"),
                url=f"http://{socket.getfqdn()}:14268",
            ),
        ]
    ).dump()
    assert tracing_out.local_app_data == expected_data
