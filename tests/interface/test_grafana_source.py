# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
from interface_tester import InterfaceTester


def test_grafana_datasource_v0_interface(grafana_datasource_tester: InterfaceTester):
    grafana_datasource_tester.configure(
        interface_name="grafana_datasource",
        interface_version=0,
    )
    grafana_datasource_tester.run()
