#!/usr/bin/env python3
# Copyright 2024 Ubuntu
# See LICENSE file for licensing details.

import json
import logging
from pathlib import Path

import yaml

from tests.integration.helpers import deploy_cluster
from tests.integration.juju import WorkloadStatus

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
TEMPO = "tempo"
PROM = "prom"
apps = [TEMPO, PROM]


def test_build_and_deploy(tempo_charm: Path, juju, tempo_resources):
    """Build the charm-under-test and deploy it together with related charms."""
    # Deploy the charms and wait for active/idle status
    deploy_cluster(juju, tempo_app=TEMPO)
    juju.deploy("prometheus-k8s", channel="edge", alias=PROM, trust=True)
    juju.integrate(TEMPO + ":metrics-endpoint", PROM + ":metrics-endpoint")

    juju.wait(
        stop=lambda status: status.all_workloads((PROM, TEMPO), WorkloadStatus.active),
        fail=lambda status: status.any_workload((TEMPO,), WorkloadStatus.error),
        timeout=600,
    )


def test_scrape_jobs(juju):
    # Check scrape jobs
    cmd = "curl -sS http://localhost:9090/api/v1/targets"
    result = juju.ssh(f"{PROM}/0", cmd)
    logger.info(result)
    result_json = json.loads(result.decode("utf-8"))
    active_targets = result_json["data"]["activeTargets"]

    for at in active_targets:
        assert at["labels"]["juju_application"] in apps


def test_rules(juju):
    # Check Rules
    cmd = "curl -sS http://localhost:9090/api/v1/rules"
    result = juju.ssh(f"{PROM}/0", cmd)
    logger.info(result)
    result_json = json.loads(result.decode("utf-8"))
    groups = result_json["data"]["groups"]

    for group in groups:
        for rule in group["rules"]:
            assert rule["labels"]["juju_application"] in apps
