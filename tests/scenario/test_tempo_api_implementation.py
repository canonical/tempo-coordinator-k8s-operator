"""Tests that assert TempoCoordinatorCharm is wired up correctly to be a tempo-api provider."""

from typing import Optional, Tuple
from unittest.mock import PropertyMock, patch

from ops.testing import Relation, State

RELATION_NAME = "tempo-api"
INTERFACE_NAME = "tempo_metadata"

# Note: if this is changed, the TempoApiAppData concrete classes below need to change their constructors to match
SAMPLE_APP_DATA = {
    "ingress_url": "http://www.ingress-url.com/",
    "direct_url": "http://www.internal-url.com/",
}

TEMPO_COORDINATOR_URL = "http://needs-changing.com/"
INGRESS_URL = "http://www.ingress-url.com/"


def local_app_data_relation_state(
    leader: bool, local_app_data: Optional[dict] = None
) -> Tuple[Relation, State]:
    """Return a testing State that has a single relation with the given local_app_data."""
    if local_app_data is None:
        local_app_data = {}
    else:
        # Scenario might edit this dict, and it could be used elsewhere
        local_app_data = dict(local_app_data)

    relation = Relation(RELATION_NAME, INTERFACE_NAME, local_app_data=local_app_data)
    relations = [relation]

    state = State(
        relations=relations,
        leader=leader,
    )

    return relation, state


@patch(
    "charm.TempoCoordinatorCharm._internal_url", PropertyMock(return_value=TEMPO_COORDINATOR_URL)
)
def test_provider_sender_sends_data_on_relation_joined(
    context, s3, all_worker, nginx_container, nginx_prometheus_exporter_container
):
    """Tests that a charm using TempoApiProvider sends the correct data on a relation joined event."""
    # Arrange
    tempo_api = Relation(RELATION_NAME, INTERFACE_NAME)
    relations = [
        tempo_api,
        s3,
        all_worker,
    ]

    state = State(
        relations=relations,
        leader=True,
        containers=[nginx_container, nginx_prometheus_exporter_container],
    )

    # Act
    with context(context.on.relation_joined(tempo_api), state=state) as manager:
        manager.run()
        expected = {
            "direct_url": TEMPO_COORDINATOR_URL,
        }

    # Assert
    assert tempo_api.local_app_data == expected


@patch("charm.TempoCoordinatorCharm._external_url", PropertyMock(return_value=INGRESS_URL))
@patch(
    "charm.TempoCoordinatorCharm._internal_url", PropertyMock(return_value=TEMPO_COORDINATOR_URL)
)
def test_provider_sender_sends_data_with_ingress_url_on_relation_joined(
    context, s3, all_worker, nginx_container, nginx_prometheus_exporter_container
):
    """Tests that a charm using TempoApiProvider with an external url sends the correct data."""
    # Arrange
    tempo_api = Relation(RELATION_NAME, INTERFACE_NAME)
    relations = [
        tempo_api,
        s3,
        all_worker,
    ]

    state = State(
        relations=relations,
        leader=True,
        containers=[nginx_container, nginx_prometheus_exporter_container],
    )

    # Act
    with context(context.on.relation_joined(tempo_api), state=state) as manager:
        manager.run()
        expected = {
            "direct_url": TEMPO_COORDINATOR_URL,
            "ingress_url": INGRESS_URL,
        }

    # Assert
    assert tempo_api.local_app_data == expected


@patch(
    "charm.TempoCoordinatorCharm._internal_url", PropertyMock(return_value=TEMPO_COORDINATOR_URL)
)
def test_provider_sends_data_on_leader_elected(
    context, s3, all_worker, nginx_container, nginx_prometheus_exporter_container
):
    """Tests that a charm using TempoApiProvider sends data on a leader elected event."""
    # Arrange
    tempo_api = Relation(RELATION_NAME, INTERFACE_NAME)
    relations = [
        tempo_api,
        s3,
        all_worker,
    ]

    state = State(
        relations=relations,
        leader=True,
        containers=[nginx_container, nginx_prometheus_exporter_container],
    )

    # Act
    with context(context.on.leader_elected(), state=state) as manager:
        manager.run()
        expected = {
            "direct_url": TEMPO_COORDINATOR_URL,
        }

    # Assert
    assert tempo_api.local_app_data == expected
