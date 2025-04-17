#!/usr/bin/env python3
# Copyright 2024 Ubuntu
# See LICENSE file for licensing details.

import json
import logging
from pathlib import Path

import jubilant
import pytest
import yaml
from jubilant import Juju

from helpers import run_command, TEMPO_APP
from tests.integration.helpers import TEMPO_RESOURCES

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
PROM = "prom"


@pytest.mark.setup
def test_deploy(juju: Juju, tempo_charm: Path):
    """Build the charm-under-test and deploy it together with related charms."""
    # Deploy the charms and wait for active/idle status
    juju.deploy(tempo_charm, TEMPO_APP, trust=True, resources=TEMPO_RESOURCES)
    juju.deploy("prometheus-k8s", PROM, trust=True)
    juju.integrate(f"{PROM}:metrics-endpoint", f"{TEMPO_APP}:metrics-endpoint")

    juju.wait(
        lambda status: jubilant.all_active(status, PROM),
        timeout=600,
    )

    juju.wait(
        lambda status: jubilant.all_blocked(status, TEMPO_APP),
        timeout=600,
    )


def test_scrape_jobs(juju: Juju):
    # Check scrape jobs
    cmd = ["curl", "-sS", "http://localhost:9090/api/v1/targets"]
    result = run_command(juju.model, PROM, 0, command=cmd)
    logger.info(result)
    result_json = json.loads(result.decode("utf-8"))

    active_targets = result_json["data"]["activeTargets"]

    for at in active_targets:
        assert at["labels"]["juju_application"] in (TEMPO_APP, PROM)


def test_rules(juju: Juju):
    # Check Rules
    cmd = ["curl", "-sS", "http://localhost:9090/api/v1/rules"]
    result = run_command(juju.model, PROM, 0, command=cmd)
    logger.info(result)
    result_json = json.loads(result.decode("utf-8"))
    groups = result_json["data"]["groups"]

    for group in groups:
        for rule in group["rules"]:
            assert rule["labels"]["juju_application"] in (TEMPO_APP, PROM)
