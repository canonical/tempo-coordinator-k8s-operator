"""tempo_api.

This implements provider and requirer sides of the tempo-api interface, which is used to communicate information
about a Tempo installation such as its internal url, external url and GRPC Port.

## Usage

### Requirer

To add this relation to your charm as a requirer, add the following to your `charmcraft.yaml` or `metadata.yaml`:

```yaml
requires:
  tempo-api:
    # The example below uses the API for when limit=1.  If you need to support multiple related applications, remove
    # this and use the list-based data accessor method.
    limit: 1
    interface: tempo_api
```

To handle the relation events in your charm, use `TempoApiRequirer`.  That object handles all relation events for
this relation, and emits a `DataChangedEvent` when data changes the charm might want to react to occur.  To set it up,
instantiate an `TempoApiRequirer` object in your charm's `__init__` method and observe the `DataChangedEvent`:

```python
class FooCharm(CharmBase):
    def __init__(self, framework):
        super().__init__(framework)
        # Create the TempoApiRequirer instance, providing the relation name you've used
        self.tempo_api = TempoApiRequirer(self, "tempo-api")
        self.framework.observe(self.tempo_api.on.data_changed, self.do_something_with_data)
```

To access the data elsewhere in the charm, use the provided data accessors.  These return `TempoApiAppData`
objects:

```python
class FooCharm(CharmBase):
    ...
    # If using limit=1
    def do_something_with_data(self):
        # Get exactly one related application's data, raising if more than one is available
        # note: if not using limit=1, see .get_data_from_all_relations()
        data = self.tempo_api.get_data()
        if data is None:
            self.log("No data available yet")

Return:
        self.log(f"Got Tempo's internal_url: {data.internal_url}")
```

### Provider

To add this relation to your charm as a provider, add the following to your `charmcraft.yaml` or `metadata.yaml`:

```yaml
provides:
  tempo-api:
    interface: tempo_api
```

To handle the relation events in your charm, use `TempoApiProvider`.  That object sends data to all related
requirers automatically when applications join.  To set it up, instantiate an `TempoApiProvider` object in your
charm's `__init__` method:

```python
class FooCharm(CharmBase):
    def __init__(self, framework):
        super().__init__(framework)
        # Create the TempoApiProvider instance, providing Tempo's internal, ingress URL and GRPC port.
        self.tempo_api = TempoApiProvider(
            charm=self,
            grpc_port=self.grpc_port,
            ingress_url=self.external_url,
            internal_url=self.internal_url,
            relation_name="tempo-api"
        )
```
"""

import json
import logging
from typing import List, MutableMapping, Optional, Type, Union

from ops import (
    BoundEvent,
    CharmBase,
    CharmEvents,
    EventBase,
    EventSource,
    Object,
)
from pydantic import BaseModel, Field, ValidationError

# The unique Charmhub library identifier, never change it
LIBID = "6d55454c9a104113b2bd01e738dd5f99"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1

PYDEPS = ["pydantic>=2"]

log = logging.getLogger(__name__)

DEFAULT_RELATION_NAME = "tempo-api"

# Helper Functions

# Note: MutableMapping is imported from the typing module and not collections.abc
# because subscripting collections.abc.MutableMapping was added in python 3.9, but
# most of our charms are based on 20.04, which has python 3.8.
_RawDatabag = MutableMapping[str, str]


# Adapted from https://github.com/canonical/cos-lib/blob/main/src/cosl/interfaces/utils.py's DatabagModelV2
def load_from_databag(model: Type[BaseModel], databag: Optional[_RawDatabag]):
    """Load a pydantic model from a Juju databag."""
    try:
        return model.model_validate_json(json.dumps(dict(databag)))  # type: ignore
    except ValidationError as e:
        msg = f"failed to validate databag: {databag}"
        if databag:
            log.debug(msg, exc_info=True)
        raise e


def dump_to_databag(
    data: BaseModel, databag: Optional[_RawDatabag] = None, clear: bool = True
) -> _RawDatabag:
    """Write the contents of a pydantic model to a Juju databag.
    :param data: the data model instance to write the data from.
    :param databag: the databag to write the data to.
    :param clear: ensure the databag is cleared before writing it.
    """
    _databag: _RawDatabag = {} if databag is None else databag

    if clear:
        _databag.clear()

    dct = data.model_dump(mode="json", by_alias=True, exclude_defaults=True, round_trip=True)  # type: ignore
    _databag.update(dct)
    return _databag


# Schema & Events
class DataChangedEvent(EventBase):
    """Emitted when relation data changes."""


class RequirerCharmEvents(CharmEvents):
    """Custom events for requirer."""

    data_changed = EventSource(DataChangedEvent)


class TempoApiAppData(BaseModel):
    """Schema for Grafana metadata."""

    ingress_url: str = Field(description="Public ingress URL")
    internal_url: str = Field(description="Internal cluster URL")
    grpc_port: str = Field(description="GRPC port of the Tempo instance")


# Requirer
class TempoApiRequirer(Object):
    """Class for handling the requirer side of the tempo-api relation."""

    on = RequirerCharmEvents()  # type: ignore[reportAssignmentType]

    def __init__(
        self,
        charm: CharmBase,
        relation_name: str = DEFAULT_RELATION_NAME,
        refresh_event: Optional[Union[BoundEvent, List[BoundEvent]]] = None,
    ) -> None:
        """
        Initialize the TempoApiRequirer object.

        Args:
            charm: The charm instance.
            relation_name: The name of the relation.
            refresh_event: An event or list of events that should trigger the library to process its relations.
                           By default, this charm already observes the relation_changed event.
        """
        super().__init__(charm, relation_name)
        self._charm = charm
        self._relation_name = relation_name

        # If a refresh event or list of events is provided, observe them.
        if refresh_event is None:
            refresh_event = []
        elif isinstance(refresh_event, BoundEvent):
            refresh_event = [refresh_event]
        for ev in refresh_event:
            self._charm.framework.observe(ev, self.on_relation_changed)

        # Always observe the relation_changed event for the given relation.
        self._charm.framework.observe(
            self._charm.on[self._relation_name].relation_changed, self.on_relation_changed
        )

    def __len__(self):
        """Return the number of related applications."""
        return len(self.get_relations())

    def on_relation_changed(self, _: EventBase) -> None:
        """Handle when the remote application data changed."""
        self.on.data_changed.emit()

    def get_relations(self):
        """Return the relation instances for applications related on this relation."""
        return self._charm.model.relations.get(self._relation_name, ())

    def get_data(self) -> Optional[TempoApiAppData]:
        """
        Return data for at most one related application.

        Useful for charms that always expect exactly one related application.  It is recommended that those charms also
        set limit=1 for that relation in charmcraft.yaml.  Returns None if no data is available (either because no
        applications are related to us, or because the related application has not sent data).

        Raises a RuntimeError if more than one application is related.
        """
        relations = self.get_relations()
        if len(relations) == 0:
            return None
        if len(relations) > 1:
            raise RuntimeError("Cannot get_data when more than one application is related.")

        raw_data = relations[0].data.get(relations[0].app)
        if not raw_data:
            return None

        # Ensure keys are strings.
        raw_data = {str(k): v for k, v in raw_data.items()}
        return load_from_databag(TempoApiAppData, raw_data)  # type: ignore[reportReturnType]

    def get_data_from_all_relations(self) -> List[Optional[TempoApiAppData]]:
        """Return a list of data objects from all related applications."""
        relations = self.get_relations()
        data_list = []
        for relation in relations:
            data_dict = relation.data.get(relation.app)
            if not data_dict:
                data_list.append(None)
                continue

            # Ensure keys are strings.
            data_dict = {str(k): v for k, v in data_dict.items()}
            try:
                data_list.append(TempoApiAppData(**data_dict))
            except ValidationError:
                data_list.append(None)
        return data_list


# Provider
class TempoApiProvider(Object):
    """Class for handling the provider side of the tempo-api relation."""

    def __init__(
        self,
        charm: CharmBase,
        grpc_port: str,
        ingress_url: str,
        internal_url: str,
        relation_name: str = DEFAULT_RELATION_NAME,
        refresh_event: Optional[Union[BoundEvent, List[BoundEvent]]] = None,
    ) -> None:
        """
        Initialize the TempoApiProvider object.

        Args:
            charm: The charm instance.
            grpc_port: The GRPC port of this Tempo instance.
            ingress_url: The URL for the Tempo ingress.
            internal_url: The URL for the Tempo internal service.
            relation_name: The name of the relation.
            refresh_event: An event or list of events that trigger data publication
                           (in addition to relation_joined and leader_elected).
        """
        super().__init__(charm, relation_name)
        self._charm = charm
        self._relation_name = relation_name
        self._data = TempoApiAppData(
            grpc_port=grpc_port, ingress_url=ingress_url, internal_url=internal_url
        )

        if refresh_event is None:
            refresh_event = []
        elif isinstance(refresh_event, BoundEvent):
            refresh_event = [refresh_event]
        for ev in refresh_event:
            self._charm.framework.observe(ev, self.handle_send_data_event)

        # Observe relation joined events.
        self._charm.framework.observe(
            self._charm.on[self._relation_name].relation_joined, self.handle_send_data_event
        )
        # Observe leader election because only the leader should send data.
        self._charm.framework.observe(self._charm.on.leader_elected, self.handle_send_data_event)

    def handle_send_data_event(self, event: BoundEvent) -> None:
        """
        Handle events that trigger sending data.

        Only the leader sends data.
        """
        if self._charm.unit.is_leader():
            self.send_data()

    def _get_relations(self):
        """Return the relation instances for this relation."""
        return self._charm.model.relations.get(self._relation_name, ())

    def send_data(self) -> None:
        """
        Publish data to all related applications.

        If the calling charm needs to handle cases where the data cannot be sent, it should observe the
        send_info_failed event.  This, however, is better handled by including a check on the is_ready method
        in the charm's collect_status event.
        """
        for relation in self._get_relations():
            dump_to_databag(self._data, relation.data[self._charm.app])

    def _is_relation_data_up_to_date(self) -> bool:
        """Check if the relation data is up-to-date across all related applications."""
        expected_data = self._data
        for relation in self._get_relations():
            try:
                app_data = load_from_databag(TempoApiAppData, relation.data[self._charm.app])
            except ValidationError:
                return False
            if app_data != expected_data:
                return False
        return True

    def is_ready(self) -> bool:
        """
        Return whether the data is published and up-to-date on all relations.

        Useful for charms that handle the collect_status event.
        """
        return self._is_relation_data_up_to_date()
