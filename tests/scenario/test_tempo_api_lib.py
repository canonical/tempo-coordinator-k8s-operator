"""Tests for the tempo-api lib requirer and provider classes, excluding their usage in the Tempo Coordinator charm."""

from typing import Optional, Tuple, Union

import pytest
from charms.tempo_coordinator_k8s.v0.tempo_api import (
    TempoApiAppData,
    TempoApiProvider,
    TempoApiRequirer,
)
from ops import CharmBase
from ops.testing import Context, Relation, State

RELATION_NAME = "app-data-relation"
INTERFACE_NAME = "app-data-interface"

# Note: if this is changed, the TempoApiAppData concrete classes below need to change their constructors to match
SAMPLE_APP_DATA = {
    "ingress_url": "http://www.ingress-url.com/",
    "direct_url": "http://www.internal-url.com/",
}
SAMPLE_APP_DATA_2 = {
    "ingress_url": "http://www.ingress-url2.com/",
    "direct_url": "http://www.internal-url2.com/",
}
SAMPLE_APP_DATA_NO_INGRESS_URL = {
    "direct_url": "http://www.internal-url.com/",
}


class TempoApiProviderCharm(CharmBase):
    META = {
        "name": "provider",
        "provides": {RELATION_NAME: {"interface": RELATION_NAME}},
    }

    def __init__(self, framework):
        super().__init__(framework)
        self.relation_provider = TempoApiProvider(
            self.model.relations,
            self.meta.relations[RELATION_NAME],
            **SAMPLE_APP_DATA,
            app=self.app,
        )


class TempoApiProviderWithoutIngressCharm(CharmBase):
    META = {
        "name": "provider",
        "provides": {RELATION_NAME: {"interface": RELATION_NAME}},
    }

    def __init__(self, framework):
        super().__init__(framework)
        self.relation_provider = TempoApiProvider(
            self.model.relations,
            self.meta.relations[RELATION_NAME],
            **SAMPLE_APP_DATA_NO_INGRESS_URL,
            app=self.app,
        )


@pytest.fixture()
def tempo_api_provider_context():
    return Context(charm_type=TempoApiProviderCharm, meta=TempoApiProviderCharm.META)


@pytest.fixture()
def tempo_api_provider_without_ingress_context():
    return Context(charm_type=TempoApiProviderWithoutIngressCharm, meta=TempoApiProviderCharm.META)


class TempoApiRequirerCharm(CharmBase):
    META = {
        "name": "requirer",
        "requires": {RELATION_NAME: {"interface": "tempo-api", "limit": 1}},
    }

    def __init__(self, framework):
        super().__init__(framework)
        self.relation_requirer = TempoApiRequirer(
            self.model.relations, relation_meta=self.meta.relations[RELATION_NAME]
        )


@pytest.fixture()
def tempo_api_requirer_context():
    return Context(charm_type=TempoApiRequirerCharm, meta=TempoApiRequirerCharm.META)


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


def test_tempo_api_provider_sends_data_correctly(tempo_api_provider_context):
    """Tests that a charm using TempoApiProvider sends the correct data during publish."""
    # Arrange
    relation, state = local_app_data_relation_state(leader=True)

    # Act
    with tempo_api_provider_context(
        # construct a charm using an event that won't trigger anything here
        tempo_api_provider_context.on.update_status(),
        state=state,
    ) as manager:
        manager.charm.relation_provider.publish()

    # Assert
    assert relation.local_app_data == SAMPLE_APP_DATA


def test_tempo_api_provider_without_ingress_sends_data_correctly(
    tempo_api_provider_without_ingress_context,
):
    """Tests that a charm using TempoApiProvider without an ingress_url sends the correct data during publish."""
    # Arrange
    relation, state = local_app_data_relation_state(leader=True)

    # Act
    with tempo_api_provider_without_ingress_context(
        # construct a charm using an event that won't trigger anything here
        tempo_api_provider_without_ingress_context.on.update_status(),
        state=state,
    ) as manager:
        manager.charm.relation_provider.publish()

    # Assert
    assert relation.local_app_data == SAMPLE_APP_DATA_NO_INGRESS_URL


@pytest.mark.parametrize(
    "relations, expected_data",
    [
        # no relations
        ([], None),
        # one empty relation
        (
            [Relation(RELATION_NAME, INTERFACE_NAME, remote_app_data={})],
            None,
        ),
        # one populated relation
        (
            [
                Relation(
                    RELATION_NAME,
                    INTERFACE_NAME,
                    remote_app_data=SAMPLE_APP_DATA,
                )
            ],
            TempoApiAppData(**SAMPLE_APP_DATA),  # pyright: ignore
        ),
        # one populated relation without ingress_url
        (
            [
                Relation(
                    RELATION_NAME,
                    INTERFACE_NAME,
                    remote_app_data=SAMPLE_APP_DATA_NO_INGRESS_URL,
                )
            ],
            TempoApiAppData(**SAMPLE_APP_DATA_NO_INGRESS_URL),  # pyright: ignore
        ),
    ],
)
def test_tempo_api_requirer_get_data(relations, expected_data, tempo_api_requirer_context):
    """Tests that TempoApiRequirer.get_data() returns correctly."""
    state = State(
        relations=relations,
        leader=False,
    )

    with tempo_api_requirer_context(
        tempo_api_requirer_context.on.update_status(), state=state
    ) as manager:
        charm = manager.charm

        data = charm.relation_requirer.get_data()
        assert are_app_data_equal(data, expected_data)


def are_app_data_equal(data1: Union[TempoApiAppData, None], data2: Union[TempoApiAppData, None]):
    """Compare two TempoApiRequirer objects, tolerating when one or both is None."""
    if data1 is None and data2 is None:
        return True
    if data1 is None or data2 is None:
        return False
    return data1.model_dump() == data2.model_dump()
