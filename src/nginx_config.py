# Copyright 2023 Canonical
# See LICENSE file for licensing details.
"""Nginx workload."""

import logging
from typing import Dict, List, cast

from charms.tempo_coordinator_k8s.v0.tracing import (
    ReceiverProtocol,
    TransportProtocolType,
    receiver_protocol_to_transport_protocol,
)
from cosl.coordinated_workers.nginx import (
    NginxLocationConfig,
)

from tempo import Tempo
from tempo_config import TempoRole

logger = logging.getLogger(__name__)


class NginxHelper:
    """Helper class to manage the nginx workload."""

    @staticmethod
    def roles_to_upstreams() -> Dict[str, Dict[str, int]]:
        distributor_upstreams = {
            protocol.replace("_", "-"): port
            for protocol, port in Tempo.receiver_ports.items()
        }
        query_upstreams = {
            protocol.replace("_", "-"): port
            for protocol, port in Tempo.server_ports.items()
        }
        return {
            TempoRole.distributor: distributor_upstreams,
            TempoRole.query_frontend: query_upstreams,
        }

    @staticmethod
    def server_ports_to_locations() -> Dict[int, List[NginxLocationConfig]]:
        locations = {}
        all_protocol_ports = {**Tempo.receiver_ports, **Tempo.server_ports}
        for protocol, port in all_protocol_ports.items():
            upstream = protocol.replace("_", "-")
            is_grpc = NginxHelper._is_protocol_grpc(protocol)
            locations.update(
                {port: [NginxLocationConfig(upstream=upstream, is_grpc=is_grpc)]}
            )

        return locations

    @staticmethod
    def _is_protocol_grpc(protocol: str) -> bool:
        """
        Return True if the given protocol is gRPC
        """
        if (
            protocol == "tempo_grpc"
            or receiver_protocol_to_transport_protocol.get(
                cast(ReceiverProtocol, protocol)
            )
            == TransportProtocolType.grpc
        ):
            return True
        return False


