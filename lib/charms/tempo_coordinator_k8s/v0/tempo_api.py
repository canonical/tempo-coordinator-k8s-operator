"""tempo_api.

This library implements endpoint wrappers for the tempo-api interface.  The tempo-api interface is used to
transfer information about an instance of Tempo, such as how to access and uniquely identify it.  Typically, this is
useful for charms that operate a Tempo instance to give other applications access to
[Tempo's HTTP API](https://grafana.com/docs/tempo/latest/api_docs/).

## Usage

### Requirer

TempoApiRequirer is a wrapper for pulling data from the tempo-api interface.  To use it in your charm:

* observe the relation-changed event for this relation wherever your charm needs to use this data (this endpoint wrapper
  DOES NOT automatically observe any events)
* wherever you need access to the data, call `TempoApiRequirer(...).get_data()`

An example implementation is:

```python
class FooCharm(CharmBase):
    def __init__(self, framework):
        super().__init__(framework)

        tempo_api = TempoApiRequirer(self.model.relations, self.meta.relations["tempo-api"])

        self.framework.observe(self.on["tempo-api"].relation_changed, self._on_tempo_api_changed)

    def do_something_with_metadata(self):
        # Get exactly one related application's data, raising if more than one is available
        data = tempo_api.get_data()
        ...
```

Where you also add relation to your `charmcraft.yaml` or `metadata.yaml` (note that TempoApiRequirer is designed for
relating to a single application and must be used with limit=1 as shown below):

```yaml
requires:
  tempo-api:
    limit: 1
    interface: tempo_api
```

### Provider

TempoApiProvider is a wrapper for publishing data to charms related using the tempo-api interface.  Note that
`TempoApiProvider` *does not* manage any events, but instead provides a `publish` method for sending data to
all related applications.  Triggering `publish` appropriately is left to the charm author, although generally you want
to do this at least during the `relation_joined` and `leader_elected` events.  An example implementation is:

```python
class FooCharm(CharmBase):
    def __init__(self, framework):
        super().__init__(framework)
        self.tempo_api = TempoApiProvider(
            relations=self.model.relations,
            relation_meta=self.meta.relations["tempo-api"],
            app=self.app,
            direct_url=self.direct_url,
            ingress_url=self.external_url,
        )

        self.framework.observe(self.on.leader_elected, self.do_something_to_publish)
        self.framework.observe(self._charm.on["tempo-api"].relation_joined, self.do_something_to_publish)
        self.framework.observe(self.on.some_event_that_changes_tempos_url, self.do_something_to_publish)
        
    def do_something_to_publish(self, e):
        self.tempo_api.publish(...)
```

Where you also add the following to your `charmcraft.yaml` or `metadata.yaml`:

```yaml
provides:
  tempo-api:
    interface: tempo_api
```
"""

import json
import logging
from typing import Optional

from ops import Application, RelationMapping, RelationMeta
from pydantic import AnyHttpUrl, BaseModel, Field

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


class TempoApiAppData(BaseModel):
    """Data model for the tempo-api interface."""

    ingress_url: Optional[AnyHttpUrl] = Field(
        default=None,
        description="The non-internal URL at which this application can be reached.  Typically, this is an ingress URL.",
    )
    direct_url: AnyHttpUrl = Field(
        description="The cluster-internal URL at which this application can be reached.  Typically, this is a"
        " Kubernetes FQDN like name.namespace.svc.cluster.local for connecting to the prometheus api"
        " from inside the cluster, with scheme."
    )


class TempoApiRequirer:
    """Endpoint wrapper for the requirer side of the tempo-api relation."""

    def __init__(
        self,
        relations: RelationMapping,
        relation_meta: RelationMeta,
    ) -> None:
        """Initialize the TempoApiRequirer object.

        This object is for accessing data from relations that use the tempo-api interface.  It **does not**
        autonomously handle the events associated with that relation.  It is up to the charm using this object to
        observe those events as they see fit.  Typically, that charm should observe this relation's relation-changed
        event.

        This object requires the relation has limit=1 set in charmcraft.yaml, otherwise it will raise a ValueError
        exception.

        Args:
            relations: The RelationMapping of a charm (typically `self.model.relations` from within a charm object).
            relation_meta: The RelationMeta object for this charm relation (typically
                           `self.meta.relations[relation_name]`).
        """
        self._charm_relation_mapping = relations
        self._relation_meta = relation_meta
        self._relation_name = self._relation_meta.relation_name

        self._validate_relation_metadata()

    @property
    def relations(self):
        """Return the relation instances for applications related to us on the monitored relation."""
        return self._charm_relation_mapping.get(self._relation_name, ())

    def get_data(self) -> Optional[BaseModel]:
        """Return data from the relation.

        Returns None if no data is available, either because no applications are related to us, or because the related
        application has not sent data.  To distinguish between these cases, check if
        len(tempo_api_requirer.relations)==0.
        """
        relations = self.relations
        if len(relations) == 0:
            return None

        raw_data_dict = relations[0].data.get(relations[0].app)
        if not raw_data_dict:
            return None

        # Static analysis errors saying the keys may not be strings.  Protect against this by converting them.
        raw_data_dict = {str(k): v for k, v in raw_data_dict.items()}

        return TempoApiAppData.model_validate_json(json.dumps(raw_data_dict))  # type: ignore

    def _validate_relation_metadata(self):
        """Validate that the provided relation has the correct metadata for this endpoint."""
        if self._relation_meta.limit != 1:
            raise ValueError(
                "TempoApiRequirer is designed for relating to a single application and must be used with limit=1."
            )


class TempoApiProvider:
    """The provider side of the tempo-api relation."""

    def __init__(
        self,
        relations: RelationMapping,
        relation_meta: RelationMeta,
        app: Application,
        direct_url: AnyHttpUrl,
        ingress_url: Optional[AnyHttpUrl] = None,
    ):
        """Initialize the TempoApiProvider object.

        This object is for serializing and sending data to a relation that uses the tempo-api interface - it does
        not automatically observe any events for that relation.  It is up to the charm using this to call publish when
        it is appropriate to do so, typically on at least the charm's leader_elected event and this relation's
        relation_joined event.

        Args:
            relations: The RelationMapping of a charm (typically `self.model.relations` from within a charm object).
            app: This application.
            relation_meta: The RelationMeta object for this charm relation (typically
               `self.meta.relations[relation_name]`).
            direct_url: The cluster-internal URL at which this application can be reached.  Typically, this is a
                        Kubernetes FQDN like name.namespace.svc.cluster.local for connecting to the prometheus api
                        from inside the cluster, with scheme.
            ingress_url: The non-internal URL at which this application can be reached.  Typically, this is an ingress
                         URL.
        """
        self._charm_relation_mapping = relations
        self._relation_meta = relation_meta
        self._data = TempoApiAppData(ingress_url=ingress_url, direct_url=direct_url)
        self._app = app
        self._relation_name = self._relation_meta.relation_name

    @property
    def relations(self):
        """Return the applications related to us under the monitored relation."""
        return self._charm_relation_mapping.get(self._relation_name, ())

    def publish(self):
        """Post tempo-api to all related applications.

        This method writes to the relation's app data bag, and thus should never be called by a unit that is not the
        leader otherwise ops will raise an exception.
        """
        info_relations = self.relations
        for relation in info_relations:
            databag = relation.data[self._app]
            databag.update(
                self._data.model_dump(
                    mode="json", by_alias=True, exclude_defaults=True, round_trip=True
                )
            )
