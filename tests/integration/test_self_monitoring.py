#!/usr/bin/env python3
# Copyright 2024 Ubuntu
# See LICENSE file for licensing details.

import json
import logging
from pathlib import Path
from textwrap import dedent

import yaml

from helpers import deploy_literal_bundle

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
TEMPO = "tempo"
PROM = "prom"
apps = [TEMPO, PROM]


def test_build_and_deploy(tempo_charm: Path, juju):
    """Build the charm-under-test and deploy it together with related charms."""

    test_bundle = dedent(
        f"""
        ---
        bundle: kubernetes
        name: test-charm
        applications:
          {TEMPO}:
            charm: {tempo_charm}
            trust: true
            scale: 1
            resources:
              nginx-image: {METADATA["resources"]["nginx-image"]["upstream-source"]}
              nginx-prometheus-exporter-image: {METADATA["resources"]["nginx-prometheus-exporter-image"]["upstream-source"]}
          {PROM}:
            charm: prometheus-k8s
            channel: edge
            scale: 1
            trust: true
        relations:
        - - {PROM}:metrics-endpoint
          - {TEMPO}:metrics-endpoint
        """
    )

    # Deploy the charm and wait for active/idle status
    deploy_literal_bundle(juju, test_bundle)  # See appendix below
    juju.wait(
        stop=lambda status: status.all_active(PROM, TEMPO),
        fail=lambda status: status.workload_status(f"{TEMPO}/0") == "error",
        timeout=600,
    )


def test_scrape_jobs(juju):
    # Check scrape jobs
    cmd = ["curl", "-sS", "http://localhost:9090/api/v1/targets"]
    result = juju.cli(PROM, 0, *cmd)
    logger.info(result)
    result_json = json.loads(result.decode("utf-8"))

    active_targets = result_json["data"]["activeTargets"]

    for at in active_targets:
        assert at["labels"]["juju_application"] in apps


def test_rules(juju):
    # Check Rules
    cmd = ["curl", "-sS", "http://localhost:9090/api/v1/rules"]
    result = juju.cli(PROM, 0, *cmd)
    logger.info(result)
    result_json = json.loads(result.decode("utf-8"))
    groups = result_json["data"]["groups"]

    for group in groups:
        for rule in group["rules"]:
            assert rule["labels"]["juju_application"] in apps
