#!/usr/bin/env python3
# Copyright 2024 Ubuntu
# See LICENSE file for licensing details.

import logging
from pathlib import Path

import yaml

from helpers import (
    WORKER_NAME,
    deploy_cluster,
    get_traces_patiently,
)
from tests.integration.juju import WorkloadStatus

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
APP_NAME = "tempo"
APP_REMOTE_NAME = "tempo-remote"
APP_WORKER_REMOTE_NAME = "tempo-remote-worker"


def test_build_and_deploy(tempo_charm: Path, tempo_resources, juju):
    # deploy cluster
    deploy_cluster(juju, tempo_charm, tempo_resources, tempo_app=APP_NAME)

    # we deploy a second tempo cluster under a different alias (but reuse s3)
    deploy_cluster(
        juju,
        tempo_charm,
        tempo_resources,
        tempo_app=APP_REMOTE_NAME,
        worker_app=APP_WORKER_REMOTE_NAME,
        deploy_s3=False,
    )

    juju.wait(
        stop=lambda status: status.all_workloads(
            (APP_NAME, WORKER_NAME), WorkloadStatus.active
        )
        and status.all_workloads(APP_REMOTE_NAME, WorkloadStatus.blocked),
        timeout=1000,
    )


def test_verify_trace_http_self(juju):
    # adjust update-status interval to generate a charm tracing span faster
    with juju.fast_forward():
        # Verify traces from `tempo` are ingested into self Tempo
        assert get_traces_patiently(
            juju.status().get_application_ip(APP_NAME),
            service_name=f"{APP_NAME}-charm",
            tls=False,
        )


def test_relate_remote_instance(juju):
    juju.integrate(APP_NAME + ":tracing", APP_REMOTE_NAME + ":self-charm-tracing")
    juju.wait(
        stop=lambda status: status.all_workloads(
            (APP_NAME, WORKER_NAME), WorkloadStatus.active
        ),
        timeout=1000,
    )


def test_verify_trace_http_remote(juju):
    # adjust update-status interval to generate a charm tracing span faster
    with juju.fast_forward():
        # Verify traces from `tempo-remote` are ingested into tempo instance
        assert get_traces_patiently(
            juju.status().get_application_ip(APP_NAME),
            service_name=f"{APP_REMOTE_NAME}-charm",
            tls=False,
        )
