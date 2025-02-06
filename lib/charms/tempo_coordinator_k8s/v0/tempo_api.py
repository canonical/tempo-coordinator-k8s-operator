"""TODO: Add a proper docstring here.

This is a placeholder docstring for this charm library. Docstrings are
presented on Charmhub and updated whenever you push a new version of the
library.

Complete documentation about creating and documenting libraries can be found
in the SDK docs at https://juju.is/docs/sdk/libraries.

See `charmcraft publish-lib` and `charmcraft fetch-lib` for details of how to
share and consume charm libraries. They serve to enhance collaboration
between charmers. Use a charmer's libraries for classes that handle
integration with their charm.

Bear in mind that new revisions of the different major API versions (v0, v1,
v2 etc) are maintained independently.  You can continue to update v0 and v1
after you have pushed v3.

Markdown is supported, following the CommonMark specification.
"""

from typing import List, Optional, Union

# import and re-export these classes from the relation_handlers module, in case the user needs them
from charm_relation_building_blocks.relation_handlers import (
    DataChangedEvent as DataChangedEvent,  # ignore: F401
)
from charm_relation_building_blocks.relation_handlers import Receiver
from charm_relation_building_blocks.relation_handlers import (
    ReceiverCharmEvents as ReceiverCharmEvents,  # ignore: F401
)
from charm_relation_building_blocks.relation_handlers import Sender
from ops import BoundEvent, CharmBase
from pydantic import BaseModel, Field

# The unique Charmhub library identifier, never change it
LIBID = "6d55454c9a104113b2bd01e738dd5f99"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1

DEFAULT_RELATION_NAME = "tempo-api"


class TempoApiAppData(BaseModel):
    """Data model for the tempo-api interface."""

    ingress_url: str = Field()
    internal_url: str = Field()
    grpc_port: int = Field()


class TempoApiRequirer(Receiver):
    """Class for handling the receiver side of the tempo-api relation."""

    # inherits the events:
    # on = ReceiverCharmEvents()  # type: ignore[reportAssignmentType]
    #

    def __init__(
        self,
        charm: CharmBase,
        relation_name: str = DEFAULT_RELATION_NAME,
        refresh_event: Optional[Union[BoundEvent, List[BoundEvent]]] = None,
    ) -> None:
        """Initialize the TempoApiRequirer object.
        Args:
            charm: The charm instance.
            relation_name: The name of the relation.
            refresh_event: An event or list of events that should trigger the library to process its relations.
                           By default, this charm already observes the relation_changed event.
        """
        super().__init__(charm, relation_name, TempoApiAppData, refresh_event)


class TempoApiProvider(Sender):
    """Class for handling the sending side of the tempo-api relation."""

    def __init__(
        self,
        charm: CharmBase,
        grpc_port: int,
        ingress_url: str,
        internal_url: str,
        relation_name: str = DEFAULT_RELATION_NAME,
        refresh_event: Optional[Union[BoundEvent, List[BoundEvent]]] = None,
    ) -> None:
        """Initialize the TempoApiProvider object.
        Args:
            charm: The charm instance.
            grpc_port: The GRPC port of this Tempo instance.
            ingress_url: The URL for the Tempo ingress.
            internal_url: The URL for the Tempo internal service.
            relation_name: The name of the relation.
            refresh_event: An event or list of events that should trigger the library to publish data to its relations.
                           By default, this charm already observes the relation_joined and on_leader_elected events.
        """
        data = TempoApiAppData(
            grpc_port=grpc_port, ingress_url=ingress_url, internal_url=internal_url
        )
        super().__init__(charm, data, relation_name, refresh_event)
