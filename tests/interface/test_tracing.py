# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import pytest
from interface_tester import InterfaceTester


def test_tracing_v2_interface(tracing_tester: InterfaceTester):
    tracing_tester.configure(
        interface_name="tracing",
        interface_version=2,
    )
    try:
        tracing_tester._RAISE_IMMEDIATELY = True
        tracing_tester.run()
    except ValueError:
        # FIXME: https://github.com/canonical/pytest-interface-tester/issues/27
        # "ValueError: Multiple endpoints found for requirer/tracing:
        # ['self-charm-tracing', 'self-workload-tracing']: cannot guess
        # which one it is we're supposed to be testing"
        pytest.xfail()
