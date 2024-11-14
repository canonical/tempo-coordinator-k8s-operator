# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
from interface_tester import InterfaceTester


def test_tracing_v2_interface(charm_tracing_tester: InterfaceTester):
    charm_tracing_tester.configure(
        interface_name="tracing",
        interface_version=2,
    )

    charm_tracing_tester._RAISE_IMMEDIATELY = True
    charm_tracing_tester.run()
