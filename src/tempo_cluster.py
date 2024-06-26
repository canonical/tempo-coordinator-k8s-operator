#!/usr/bin/env python3
# Copyright 2024 Canonical
# See LICENSE file for licensing details.

"""This module contains an endpoint wrapper class for the requirer side of the ``tempo-cluster`` relation.

As this relation is cluster-internal and not intended for third-party charms to interact with
`tempo-coordinator-k8s`, its only user will be the tempo-worker-k8s charm. As such,
it does not live in a charm lib as most other relation endpoint wrappers do.
"""
import collections
import json
import logging
from enum import Enum, unique
from typing import Any, Dict, List, MutableMapping, Optional, Set

import ops
import pydantic

# The only reason we need the tracing lib is this enum. Not super nice.
from charms.tempo_k8s.v2.tracing import ReceiverProtocol
from ops import EventSource, Object, ObjectEvents
from pydantic import BaseModel, ConfigDict

log = logging.getLogger("tempo_cluster")

DEFAULT_ENDPOINT_NAME = "tempo-cluster"
BUILTIN_JUJU_KEYS = {"ingress-address", "private-address", "egress-subnets"}


# TODO: inherit enum.StrEnum when jammy is no longer supported.
# https://docs.python.org/3/library/enum.html#enum.StrEnum
@unique
class TempoRole(str, Enum):
    """Tempo component role names.

    References:
     arch:
      -> https://grafana.com/docs/tempo/latest/operations/architecture/
     config:
      -> https://grafana.com/docs/tempo/latest/configuration/#server
    """

    # scalable-single-binary is a bit too long to type
    all = "all"  # default, meta-role. gets remapped to scalable-single-binary by the worker.

    querier = "querier"
    query_frontend = "query-frontend"
    ingester = "ingester"
    distributor = "distributor"
    compactor = "compactor"
    metrics_generator = "metrics-generator"

    @property
    def all_nonmeta(self):
        return (
            TempoRole.querier,
            TempoRole.query_frontend,
            TempoRole.ingester,
            TempoRole.distributor,
            TempoRole.compactor,
            TempoRole.metrics_generator,
        )


class ConfigReceivedEvent(ops.EventBase):
    """Event emitted when the "tempo-cluster" provider has shared a new tempo config."""

    config: Dict[str, Any]
    """The tempo config."""

    def __init__(self, handle: ops.framework.Handle, config: Dict[str, Any]):
        super().__init__(handle)
        self.config = config

    def snapshot(self) -> Dict[str, Any]:
        """Used by the framework to serialize the event to disk.

        Not meant to be called by charm code.
        """
        return {"config": json.dumps(self.config)}

    def restore(self, snapshot: Dict[str, Any]):
        """Used by the framework to deserialize the event from disk.

        Not meant to be called by charm code.
        """
        self.relation = json.loads(snapshot["config"])  # noqa


class TempoClusterError(Exception):
    """Base class for exceptions raised by this module."""


class DataValidationError(TempoClusterError):
    """Raised when relation databag validation fails."""


class DatabagAccessPermissionError(TempoClusterError):
    """Raised when a follower attempts to write leader settings."""


class JujuTopology(pydantic.BaseModel):
    """JujuTopology."""

    model: str
    unit: str
    # ...


# DatabagModel implementation from traefik.v1.ingress charm lib.
PYDANTIC_IS_V1 = int(pydantic.version.VERSION.split(".")[0]) < 2
if PYDANTIC_IS_V1:

    class DatabagModel(BaseModel):  # type: ignore
        """Base databag model."""

        class Config:
            """Pydantic config."""

            allow_population_by_field_name = True
            """Allow instantiating this class by field name (instead of forcing alias)."""

        @classmethod
        def load(cls, databag: MutableMapping):
            """Load this model from a Juju databag."""

            try:
                data = {
                    k: json.loads(v)
                    for k, v in databag.items()
                    # Don't attempt to parse model-external values
                    if k in {f.alias for f in cls.__fields__.values()}  # type: ignore
                }
            except json.JSONDecodeError as e:
                msg = f"invalid databag contents: expecting json. {databag}"
                log.error(msg)
                raise DataValidationError(msg) from e

            try:
                return cls.parse_raw(json.dumps(data))  # type: ignore
            except pydantic.ValidationError as e:
                msg = f"failed to validate databag: {databag}"
                log.debug(msg, exc_info=True)
                raise DataValidationError(msg) from e

        def dump(self, databag: Optional[MutableMapping] = None, clear: bool = True):
            """Write the contents of this model to Juju databag.

            :param databag: the databag to write the data to.
            :param clear: ensure the databag is cleared before writing it.
            """
            if clear and databag:
                databag.clear()

            if databag is None:
                databag = {}

            for key, value in self.dict(by_alias=True, exclude_defaults=True).items():  # type: ignore
                databag[key] = json.dumps(value)

            return databag

else:
    from pydantic import ConfigDict

    class DatabagModel(BaseModel):
        """Base databag model."""

        model_config = ConfigDict(
            # tolerate additional keys in databag
            extra="ignore",
            # Allow instantiating this class by field name (instead of forcing alias).
            populate_by_name=True,
        )  # type: ignore
        """Pydantic config."""

        @classmethod
        def load(cls, databag: MutableMapping):
            """Load this model from a Juju databag."""

            try:
                data = {
                    k: json.loads(v)
                    for k, v in databag.items()
                    # Don't attempt to parse model-external values
                    if k in {(f.alias or n) for n, f in cls.model_fields.items()}  # type: ignore
                }
            except json.JSONDecodeError as e:
                msg = f"invalid databag contents: expecting json. {databag}"
                log.error(msg)
                raise DataValidationError(msg) from e

            try:
                return cls.model_validate_json(json.dumps(data))  # type: ignore
            except pydantic.ValidationError as e:
                msg = f"failed to validate databag: {databag}"
                log.debug(msg, exc_info=True)
                raise DataValidationError(msg) from e

        def dump(self, databag: Optional[MutableMapping] = None, clear: bool = True):
            """Write the contents of this model to Juju databag.

            :param databag: the databag to write the data to.
            :param clear: ensure the databag is cleared before writing it.
            """
            if clear and databag:
                databag.clear()

            if databag is None:
                databag = {}

            dct = self.model_dump(mode="json", by_alias=True, exclude_defaults=True)  # type: ignore
            databag.update({k: json.dumps(v) for k, v in dct.items()})
            return databag


class TempoClusterRequirerAppData(DatabagModel):
    """TempoClusterRequirerAppData."""

    role: TempoRole


class TempoClusterRequirerUnitData(DatabagModel):
    """TempoClusterRequirerUnitData."""

    juju_topology: JujuTopology
    address: str


class TempoClusterProviderAppData(DatabagModel):
    """TempoClusterProviderAppData."""

    tempo_config: Dict[str, Any]
    loki_endpoints: Optional[Dict[str, str]] = None
    ca_cert: Optional[str] = None
    server_cert: Optional[str] = None
    privkey_secret_id: Optional[str] = None
    tempo_receiver: Optional[Dict[ReceiverProtocol, str]] = None


class TempoClusterChangedEvent(ops.EventBase):
    """Event emitted when any "tempo-cluster" relation event fires."""


class TempoClusterProviderEvents(ObjectEvents):
    """Events emitted by the TempoClusterProvider "tempo-cluster" endpoint wrapper."""

    changed = EventSource(TempoClusterChangedEvent)


class TempoClusterProvider(Object):
    """``tempo-cluster`` provider endpoint wrapper."""

    on = TempoClusterProviderEvents()  # type: ignore

    def __init__(
        self,
        charm: ops.CharmBase,
        key: Optional[str] = None,
        endpoint: str = DEFAULT_ENDPOINT_NAME,
    ):
        super().__init__(charm, key or endpoint)
        self._charm = charm
        self.juju_topology = {"unit": self.model.unit.name, "model": self.model.name}

        # filter out common unhappy relation states
        self._relations: List[ops.Relation] = [
            rel for rel in self.model.relations[endpoint] if (rel.app and rel.data)
        ]

        # we coalesce all tempo-cluster-relation-* events into a single cluster-changed API.
        # the coordinator uses a common exit hook reconciler, that's why.
        self.framework.observe(
            self._charm.on[endpoint].relation_joined, self._on_tempo_cluster_changed
        )
        self.framework.observe(
            self._charm.on[endpoint].relation_changed, self._on_tempo_cluster_changed
        )
        self.framework.observe(
            self._charm.on[endpoint].relation_departed, self._on_tempo_cluster_changed
        )
        self.framework.observe(
            self._charm.on[endpoint].relation_broken, self._on_tempo_cluster_changed
        )

    def _on_tempo_cluster_changed(self, _):
        self.on.changed.emit()

    def publish_privkey(self, label: str) -> str:
        """Grant the secret containing the privkey to all relations, and return the secret ID."""
        secret = self.model.get_secret(label=label)
        for relation in self._relations:
            secret.grant(relation)
        # can't return secret.id because secret was obtained by label, and so
        # we don't have an ID unless we fetch it
        return secret.get_info().id

    def publish_data(
        self,
        tempo_config: Dict[str, Any],
        tempo_receiver: Optional[Dict[ReceiverProtocol, Any]] = None,
        ca_cert: Optional[str] = None,
        server_cert: Optional[str] = None,
        privkey_secret_id: Optional[str] = None,
        loki_endpoints: Optional[Dict[str, str]] = None,
    ) -> None:
        """Publish the tempo config to all related tempo worker clusters."""
        for relation in self._relations:
            if relation:
                local_app_databag = TempoClusterProviderAppData(
                    tempo_config=tempo_config,
                    loki_endpoints=loki_endpoints,
                    tempo_receiver=tempo_receiver,
                    ca_cert=ca_cert,
                    server_cert=server_cert,
                    privkey_secret_id=privkey_secret_id,
                )
                local_app_databag.dump(relation.data[self.model.app])

    @property
    def has_workers(self) -> bool:
        """Return whether this tempo coordinator has any connected workers."""
        # we use the presence of relations instead of addresses, because we want this
        # check to fail early
        return bool(self._relations)

    def gather_addresses_by_role(self) -> Dict[str, Set[str]]:
        """Go through the worker's unit databags to collect all the addresses published by the units, by role."""
        data = collections.defaultdict(set)
        for relation in self._relations:

            if not relation.app:
                log.debug(f"skipped {relation} as .app is None")
                continue

            try:
                worker_app_data = TempoClusterRequirerAppData.load(relation.data[relation.app])
            except DataValidationError as e:
                log.info(f"invalid databag contents: {e}")
                continue

            for worker_unit in relation.units:
                try:
                    worker_data = TempoClusterRequirerUnitData.load(relation.data[worker_unit])
                    unit_address = worker_data.address
                    data[worker_app_data.role].add(unit_address)
                except DataValidationError as e:
                    log.info(f"invalid databag contents: {e}")
                    continue

        return data

    def gather_addresses(self) -> Set[str]:
        """Go through the worker's unit databags to collect all the addresses published by the units."""
        data = set()
        addresses_by_role = self.gather_addresses_by_role()
        for role, address_set in addresses_by_role.items():
            data.update(address_set)

        return data

    def gather_roles(self) -> Dict[TempoRole, int]:
        """Go through the worker's app databags and sum the available application roles."""
        data = collections.Counter()
        for relation in self._relations:
            if relation.app:
                remote_app_databag = relation.data[relation.app]
                try:
                    worker_role: TempoRole = TempoClusterRequirerAppData.load(
                        remote_app_databag
                    ).role
                except DataValidationError as e:
                    log.debug(f"invalid databag contents: {e}")
                    continue

                # the number of units with each role is the number of remote units
                role_n = len(relation.units)  # exclude this unit
                if worker_role is TempoRole.all:
                    for role in [r for r in TempoRole if r is not TempoRole.all]:
                        data[role] += role_n
                    continue

                data[worker_role] += role_n

        dct = dict(data)
        # exclude all roles from the count, if any slipped through
        if TempoRole.all in data:
            del data[TempoRole.all]
        return dct
