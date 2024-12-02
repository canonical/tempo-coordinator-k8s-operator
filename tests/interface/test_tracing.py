# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
from interface_tester import InterfaceTester

from tests.interface.conftest import tracing_tester


def test_tracing_v2_interface(tracing_tester: InterfaceTester):
    tracing_tester.configure(
        interface_name="tracing",
        interface_version=2,
    )
    tracing_tester.run()
