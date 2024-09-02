# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import pytest
from cosl.coordinated_workers.interface import ClusterRequirerUnitData, ClusterRequirerAppData
from interface_tester import InterfaceTester
from ops.pebble import Layer
from scenario import Relation
from scenario.state import Container, State, PeerRelation

from charm import TempoCoordinatorCharm
from charms.tempo_k8s.v1.charm_tracing import charm_tracing_disabled

tempo_container = Container(
    name="tempo",
    can_connect=True,
    layers={
        "foo": Layer(
            {
                "summary": "foo",
                "description": "bar",
                "services": {
                    "tempo": {
                        "startup": "enabled",
                        "current": "active",
                        "name": "tempo",
                    }
                },
                "checks": {},
            }
        )
    },
)

s3_relation = Relation("s3", remote_app_data={
    "access-key": "key",
    "bucket": "tempo",
    "endpoint": "http://1.2.3.4:9000",
    "secret-key": "soverysecret",
})
cluster_relation = Relation(
    "cluster",
    remote_app_data=ClusterRequirerAppData(role="all").dump(),
    remote_units_data={
        0: ClusterRequirerUnitData(
            address="http://example.com",
            juju_topology={
                "application": "app",
                "unit": "unit",
                "charm_name": "charmname"

            }).dump()
    }
)

peers = PeerRelation("peers", peers_data={1: {}})


# Interface tests are centrally hosted at https://github.com/canonical/charm-relation-interfaces.
# this fixture is used by the test runner of charm-relation-interfaces to test tempo's compliance
# with the interface specifications.
# DO NOT MOVE OR RENAME THIS FIXTURE! If you need to, you'll need to open a PR on
# https://github.com/canonical/charm-relation-interfaces and change tempo's test configuration
# to include the new identifier/location.
@pytest.fixture
def cluster_tester(interface_tester: InterfaceTester):
    with charm_tracing_disabled():
        # FIXME: expose publicly

        # # if we're testing the tracing interface:
        # if interface_name == "tracing":
        #     interface_tester.configure(
        #         charm_type=TempoCoordinatorCharm,
        #         state_template=State(leader=True, containers=[tempo_container],
        #                              relations=[peers, s3_relation, cluster_relation]),
        #     )
        #     yield interface_tester
        #
        # # if we're testing the s3 interface:
        # elif interface_name == "s3":
        #     interface_tester.configure(
        #         charm_type=TempoCoordinatorCharm,
        #         state_template=State(leader=True, containers=[tempo_container], relations=[peers, cluster_relation]),
        #     )
        #     yield interface_tester

        # if we're testing the cluster interface:
        interface_tester.configure(
            charm_type=TempoCoordinatorCharm,
            state_template=State(leader=True, containers=[tempo_container], relations=[peers, s3_relation]),
        )
        yield interface_tester
