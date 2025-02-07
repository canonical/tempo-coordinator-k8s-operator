#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
import json
import logging

from charms.tempo_coordinator_k8s.v0.tempo_api import TempoApiRequirer as Requirer
from ops import ActionEvent, CollectStatusEvent, WaitingStatus
from ops.charm import CharmBase
from ops.main import main
from ops.model import ActiveStatus

logger = logging.getLogger(__name__)


class MetadataTester(CharmBase):
    def __init__(self, framework):
        super().__init__(framework)
        self.tempo_api_rel = Requirer(self, "tempo-api")

        self.framework.observe(self.on.collect_unit_status, self.on_collect_unit_status)
        self.framework.observe(self.on.get_data_action, self.on_get_data)

    def on_collect_unit_status(self, event: CollectStatusEvent):
        statuses = []
        if len(self.tempo_api_rel) == 0:
            statuses.append(WaitingStatus("Waiting for tempo-api relation"))
        else:
            relation_data = self.tempo_api_rel.get_data()
            if relation_data is None:
                statuses.append(
                    WaitingStatus("tempo-api relation found but no data available yet")
                )
            else:
                statuses.append(
                    ActiveStatus(
                        f"Alive with tempo-api relation data: '{relation_data.model_dump()}'"
                    )
                )
        for status in statuses:
            event.add_status(status)

    def on_get_data(self, event: ActionEvent):
        relation_data = self.tempo_api_rel.get_data()
        if relation_data is None:
            relation_data = {}
        else:
            relation_data = relation_data.model_dump()
        event.set_results({"relation-data": json.dumps(relation_data)})


if __name__ == "__main__":
    main(MetadataTester)
