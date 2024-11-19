#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charmed Operator for Tempo; a lightweight object storage based tracing backend."""

import logging
import re
import socket
from pathlib import Path
from subprocess import CalledProcessError, getoutput
from typing import Dict, List, Optional, Set, Tuple, cast, get_args

import ops
from charms.grafana_k8s.v0.grafana_source import GrafanaSourceProvider
from charms.prometheus_k8s.v1.prometheus_remote_write import (
    PrometheusRemoteWriteConsumer,
)
from charms.tempo_coordinator_k8s.v0.charm_tracing import trace_charm
from charms.tempo_coordinator_k8s.v0.tracing import (
    ReceiverProtocol,
    TracingEndpointProvider,
    TransportProtocolType,
    receiver_protocol_to_transport_protocol,
)
from charms.traefik_k8s.v0.traefik_route import TraefikRouteRequirer
from cosl.coordinated_workers.coordinator import ClusterRolesConfig, Coordinator
from cosl.coordinated_workers.interface import DatabagModel
from cosl.coordinated_workers.nginx import CA_CERT_PATH, CERT_PATH, KEY_PATH
from ops import CollectStatusEvent
from ops.charm import CharmBase

from nginx_config import NginxConfig
from tempo import Tempo
from tempo_config import TEMPO_ROLES_CONFIG

logger = logging.getLogger(__name__)
PEERS_RELATION_ENDPOINT_NAME = "peers"


class TempoCoordinator(Coordinator):
    """A Tempo coordinator class that inherits from the Coordinator class."""

    @property
    def _charm_tracing_receivers_urls(self) -> Dict[str, str]:
        """Override with custom enabled and requested receivers."""
        # if related to a remote instance, return the remote instance's endpoints
        if self.charm_tracing.is_ready():
            return super()._charm_tracing_receivers_urls
        # return this instance's endpoints
        return self._charm.requested_receivers_urls()  # type: ignore

    @property
    def _workload_tracing_receivers_urls(self) -> Dict[str, str]:
        """Override with custom enabled and requested receivers."""
        # if related to a remote instance, return the remote instance's endpoints
        if self.workload_tracing.is_ready():
            return super()._workload_tracing_receivers_urls
        # return this instance's endpoints
        return self._charm.requested_receivers_urls()  # type: ignore


class PeerData(DatabagModel):
    """Databag model for the "peers" relation between coordinator units."""

    fqdn: str
    """FQDN hostname of this coordinator unit."""


@trace_charm(
    tracing_endpoint="tempo_otlp_http_endpoint",
    server_cert="server_ca_cert",
    extra_types=(Tempo, TracingEndpointProvider, Coordinator, ClusterRolesConfig),
    # use PVC path for buffer data, so we don't lose it on pod churn
    buffer_path=Path("/tempo-data/.charm_tracing_buffer.raw"),
)
class TempoCoordinatorCharm(CharmBase):
    """Charmed Operator for Tempo; a distributed tracing backend."""

    def __init__(self, *args):
        super().__init__(*args)

        self.ingress = TraefikRouteRequirer(
            self,
            self.model.get_relation("ingress"),  # type: ignore
            "ingress",
        )  # type: ignore
        self.tempo = Tempo(
            requested_receivers=self._requested_receivers,
            retention_period_hours=self._trace_retention_period_hours,
        )
        # set alert_rules_path="", as we don't want to populate alert rules into the relation databag
        # we only need `self._remote_write.endpoints`
        self._remote_write = PrometheusRemoteWriteConsumer(self, alert_rules_path="")
        # set the open ports for this unit
        self.unit.set_ports(*self.tempo.all_ports.values())

        self.tracing = TracingEndpointProvider(self, external_url=self._external_url)

        self.framework.observe(self.on.collect_unit_status, self._on_collect_status)

        self.coordinator = TempoCoordinator(
            charm=self,
            roles_config=TEMPO_ROLES_CONFIG,
            external_url=self._external_url,
            worker_metrics_port=self.tempo.tempo_http_server_port,
            endpoints={
                "certificates": "certificates",
                "cluster": "tempo-cluster",
                "grafana-dashboards": "grafana-dashboard",
                "logging": "logging",
                "metrics": "metrics-endpoint",
                "s3": "s3",
                "charm-tracing": "self-charm-tracing",
                "workload-tracing": "self-workload-tracing",
            },
            nginx_config=NginxConfig(server_name=self.hostname).config,
            workers_config=self.tempo.config,
            resources_requests=self.get_resources_requests,
            container_name="charm",
            remote_write_endpoints=self.remote_write_endpoints,  # type: ignore
            # TODO: future Tempo releases would be using otlp_xx protocols instead.
            workload_tracing_protocols=["jaeger_thrift_http"],
        )

        # configure this tempo as a datasource in grafana
        self.grafana_source_provider = GrafanaSourceProvider(
            self,
            source_type="tempo",
            source_url=self._external_http_server_url,
            refresh_event=[
                # refresh the source url when TLS config might be changing
                self.on[
                    self.coordinator.cert_handler.certificates_relation_name
                ].relation_changed,
                # or when ingress changes
                self.ingress.on.ready,
            ],
        )

        # peer
        self.framework.observe(
            self.on[PEERS_RELATION_ENDPOINT_NAME].relation_created,
            self._on_peers_relation_created,
        )

        # refuse to handle any other event as we can't possibly know what to do.
        if not self.coordinator.can_handle_events:
            # logging is handled by the Coordinator object
            return

        # do this regardless of what event we are processing
        self._reconcile()

        # actions
        self.framework.observe(
            self.on.list_receivers_action,
            self._on_list_receivers_action,
        )

    ######################
    # UTILITY PROPERTIES #
    ######################
    @property
    def peers(self):
        """Fetch the "peers" peer relation."""
        return self.model.get_relation(PEERS_RELATION_ENDPOINT_NAME)

    @property
    def _external_hostname(self) -> str:
        """Return the external hostname."""
        return re.sub(r"^https?:\/\/", "", self._external_url)

    @property
    def hostname(self) -> str:
        """Unit's hostname."""
        return socket.getfqdn()

    @property
    def _external_http_server_url(self) -> str:
        """External url of the http(s) server."""
        return f"{self._external_url}:{self.tempo.tempo_http_server_port}"

    @property
    def _external_url(self) -> str:
        """Return the external url."""
        if (
            self.ingress.is_ready()
            and self.ingress.scheme
            and self.ingress.external_host
        ):
            ingress_url = f"{self.ingress.scheme}://{self.ingress.external_host}"
            logger.debug("This unit's ingress URL: %s", ingress_url)
            return ingress_url

        # If we do not have an ingress, then use the pod hostname.
        # The reason to prefer this over the pod name (which is the actual
        # hostname visible from the pod) or a K8s service, is that those
        # are routable virtually exclusively inside the cluster (as they rely)
        # on the cluster's DNS service, while the ip address is _sometimes_
        # routable from the outside, e.g., when deploying on MicroK8s on Linux.
        return self._internal_url

    @property
    def _scheme(self) -> str:
        """Return the URI scheme that should be used when communicating with this unit."""
        scheme = "http"
        if self.are_certificates_on_disk:
            scheme = "https"
        return scheme

    @property
    def _internal_url(self) -> str:
        """Return the locally addressable, FQDN based unit address."""
        return f"{self._scheme}://{self.hostname}"

    @property
    def are_certificates_on_disk(self) -> bool:
        """Return True if the certificates files are on disk."""
        nginx_container = self.unit.get_container("nginx")

        return (
            nginx_container.can_connect()
            and nginx_container.exists(CERT_PATH)
            and nginx_container.exists(KEY_PATH)
            and nginx_container.exists(CA_CERT_PATH)
        )

    @property
    def enabled_receivers(self) -> Set[str]:
        """Extra receivers enabled through config."""
        enabled_receivers = set()
        # otlp_http is needed by charm_tracing
        enabled_receivers.add("otlp_http")
        # jaeger_thrift_http is needed by Tempo's internal workload traces
        enabled_receivers.add("jaeger_thrift_http")
        enabled_receivers.update(
            [
                receiver
                for receiver in get_args(ReceiverProtocol)
                if self.config.get(f"always_enable_{receiver}") is True
            ],
        )
        return enabled_receivers

    ##################
    # EVENT HANDLERS #
    ##################
    def _on_peers_relation_created(self, _: ops.RelationCreatedEvent):
        self.update_peer_data()

    def _on_list_receivers_action(self, event: ops.ActionEvent):
        res = {}
        for receiver in self._requested_receivers():
            res[receiver.replace("_", "-")] = self.get_receiver_url(receiver)
        event.set_results(res)

    def _on_collect_status(self, e: CollectStatusEvent):
        # add Tempo coordinator-specific statuses
        if (
            "metrics-generator" in self.coordinator.cluster.gather_roles()
            and not self.remote_write_endpoints()
        ):
            e.add_status(
                ops.ActiveStatus(
                    "metrics-generator disabled. Add a relation over send-remote-write",
                ),
            )

    ###################
    # UTILITY METHODS #
    ###################

    def update_peer_data(self) -> None:
        """Update peer unit data bucket with this unit's hostname."""
        if self.peers and self.peers.data:
            PeerData(fqdn=self.hostname).dump(self.peers.data[self.unit])

    def get_peer_data(self, unit: ops.Unit) -> Optional[PeerData]:
        """Get peer data from a given unit data bucket."""
        if not (self.peers and self.peers.data):
            return None

        return PeerData.load(self.peers.data.get(unit, {}))

    def _update_ingress_relation(self) -> None:
        """Make sure the traefik route is up-to-date."""
        if not self.unit.is_leader():
            return

        if self.ingress.is_ready():
            self.ingress.submit_to_traefik(
                self._ingress_config,
                static=self._static_ingress_config,
            )

    def _update_tracing_relations(self) -> None:
        tracing_relations = self.model.relations["tracing"]
        if not tracing_relations:
            # todo: set waiting status and configure tempo to run without receivers if possible,
            #  else perhaps postpone starting the workload at all.
            logger.warning("no tracing relations: Tempo has no receivers configured.")
            return

        requested_receivers = self._requested_receivers()
        # publish requested protocols to all relations
        if self.unit.is_leader():
            self.tracing.publish_receivers(
                [(p, self.get_receiver_url(p)) for p in requested_receivers],
            )

    def _requested_receivers(self) -> Tuple[ReceiverProtocol, ...]:
        """List what receivers we should activate, based on the active tracing relations and config-enabled extra receivers."""
        # we start with the sum of the requested endpoints from the requirers
        requested_protocols = set(self.tracing.requested_protocols())

        # update with enabled extra receivers
        requested_protocols.update(self.enabled_receivers)
        # and publish only those we support
        requested_receivers = requested_protocols.intersection(
            set(self.tempo.receiver_ports),
        )
        return tuple(requested_receivers)

    @property
    def _trace_retention_period_hours(self) -> int:
        """Trace retention period for the compactor."""
        # if unset, defaults to 30 days
        return cast(int, self.config["retention-period"])

    def server_ca_cert(self) -> str:
        """For charm tracing."""
        return CA_CERT_PATH

    def tempo_otlp_http_endpoint(self) -> Optional[str]:
        """Endpoint at which the charm tracing information will be forwarded."""
        # the charm container and the tempo workload container have apparently the same
        # IP, so we can talk to tempo at localhost.
        if hasattr(self, "coordinator") and self.coordinator.charm_tracing.is_ready():
            return self.coordinator.charm_tracing.get_endpoint("otlp_http")
        # In absence of another Tempo instance, we don't want to lose this instance's charm traces
        elif self.is_workload_ready():
            return f"{self._internal_url}:{self.tempo.receiver_ports['otlp_http']}"

    def requested_receivers_urls(self) -> Dict[str, str]:
        """Endpoints to which the workload (and the worker charm) can push traces to."""
        return {
            receiver: self.get_receiver_url(receiver)
            for receiver in self._requested_receivers()
        }

    @property
    def _static_ingress_config(self) -> dict:
        entry_points = {}
        for protocol, port in self.tempo.all_ports.items():
            sanitized_protocol = protocol.replace("_", "-")
            entry_points[sanitized_protocol] = {"address": f":{port}"}

        return {"entryPoints": entry_points}

    @property
    def _ingress_config(self) -> dict:
        """Build a raw ingress configuration for Traefik."""
        http_routers = {}
        http_services = {}
        for protocol, port in self.tempo.all_ports.items():
            sanitized_protocol = protocol.replace("_", "-")
            http_routers[
                f"juju-{self.model.name}-{self.model.app.name}-{sanitized_protocol}"
            ] = {
                "entryPoints": [sanitized_protocol],
                "service": f"juju-{self.model.name}-{self.model.app.name}-service-{sanitized_protocol}",
                # TODO better matcher
                "rule": "ClientIP(`0.0.0.0/0`)",
            }
            if (
                protocol == "tempo_grpc"
                or receiver_protocol_to_transport_protocol.get(
                    cast(ReceiverProtocol, protocol),
                )
                == TransportProtocolType.grpc
            ) and not self.coordinator.tls_available:
                # to send traces to unsecured GRPC endpoints, we need h2c
                # see https://doc.traefik.io/traefik/v2.0/user-guides/grpc/#with-http-h2c
                http_services[
                    f"juju-{self.model.name}-{self.model.app.name}-service-{sanitized_protocol}"
                ] = {
                    "loadBalancer": {
                        "servers": self._build_lb_server_config("h2c", port),
                    },
                }
            else:
                # anything else, including secured GRPC, can use _internal_url
                # ref https://doc.traefik.io/traefik/v2.0/user-guides/grpc/#with-https
                http_services[
                    f"juju-{self.model.name}-{self.model.app.name}-service-{sanitized_protocol}"
                ] = {
                    "loadBalancer": {
                        "servers": self._build_lb_server_config(self._scheme, port),
                    },
                }
        return {
            "http": {
                "routers": http_routers,
                "services": http_services,
            },
        }

    def _build_lb_server_config(self, scheme: str, port: int) -> List[Dict[str, str]]:
        """Build the server portion of the loadbalancer config of Traefik ingress."""

        def to_url(fqdn: str):
            return {"url": f"{scheme}://{fqdn}:{port}"}

        urls = [to_url(self.hostname)]
        if self.peers:
            for peer in self.peers.units:
                peer_data = self.get_peer_data(peer)
                if peer_data:
                    urls.append(to_url(peer_data.fqdn))

        return urls

    def get_receiver_url(self, protocol: ReceiverProtocol):
        """Return the receiver endpoint URL based on the protocol.

        if ingress is used, return endpoint provided by the ingress instead.
        """
        protocol_type = receiver_protocol_to_transport_protocol.get(protocol)
        # ingress.is_ready returns True even when traefik hasn't sent any data yet
        has_ingress = (
            self.ingress.is_ready()
            and self.ingress.external_host
            and self.ingress.scheme
        )
        receiver_port = self.tempo.receiver_ports[protocol]

        if has_ingress:
            url = (
                self.ingress.external_host
                if protocol_type == TransportProtocolType.grpc
                else f"{self.ingress.scheme}://{self.ingress.external_host}"
            )
        else:
            url = (
                self.hostname
                if protocol_type == TransportProtocolType.grpc
                else self._internal_url
            )

        return f"{url}:{receiver_port}"

    def is_workload_ready(self):
        """Whether the tempo built-in readiness check reports 'ready'."""
        if self.coordinator.tls_available:
            tls, s = f" --cacert {CA_CERT_PATH}", "s"
        else:
            tls = s = ""

        # cert is for fqdn/ingress, not for IP
        cmd = f"curl{tls} http{s}://{self.coordinator.hostname}:{self.tempo.tempo_http_server_port}/ready"

        try:
            out = getoutput(cmd).split("\n")[-1]
        except (CalledProcessError, IndexError):
            return False
        return out == "ready"

    def get_resources_requests(self, _) -> Dict[str, str]:
        """Return a dictionary for the "requests" portion of the resources requirements."""
        return {"cpu": "50m", "memory": "100Mi"}

    def remote_write_endpoints(self):
        """Return the remote-write endpoints."""
        return self._remote_write.endpoints

    def _reconcile(self):
        # This method contains unconditional update logic, i.e. logic that should be executed
        # regardless of the event we are processing.
        # reason is, if we miss these events because our coordinator cannot process events (inconsistent status),
        # we need to 'remember' to run this logic as soon as we become ready, which is hard and error-prone
        self._update_ingress_relation()
        self._update_tracing_relations()


if __name__ == "__main__":  # pragma: nocover
    from ops import main

    main(TempoCoordinatorCharm)  # noqa
