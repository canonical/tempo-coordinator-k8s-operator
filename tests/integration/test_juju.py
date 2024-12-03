from tests.integration.juju import (
    AgentStatus,
    Juju,
    WorkloadStatus,
    generate_random_model_name,
)

TRAEFIK = "traefik"


def test_model_mgmt():
    m0 = Juju()
    m1 = m0.add_model(generate_random_model_name())
    m2 = m1.add_model(generate_random_model_name())

    assert not m0.model  # unbound juju
    assert m1.model
    assert m2.model
    assert m2.model != m1.model

    m1.destroy_model()
    m2.destroy_model()


def test_deploy(juju):
    juju.deploy("traefik-k8s", alias=TRAEFIK, trust=True)


def test_wait_active(juju):
    juju.wait(
        stop=lambda status: status.all_workloads(TRAEFIK, WorkloadStatus.active)
        and status.all_agents(TRAEFIK, AgentStatus.idle),
        timeout=600,
        soak=1,
    )


def test_unit_ips(juju):
    js = juju.status()
    uips = js.get_unit_ips(TRAEFIK)
    assert len(uips) == 1
    assert uips[TRAEFIK + "/0"]


def test_app_ip(juju):
    js = juju.status()
    assert js.get_application_ip(TRAEFIK)


def test_leader_name(juju):
    js = juju.status()
    assert js.get_leader_name(TRAEFIK) == f"{TRAEFIK}/0"


def test_config(juju):
    cfg_key = "external_hostname"

    old_cfg = juju.application_config_get(TRAEFIK)
    assert old_cfg.charm.get(cfg_key) is None

    juju.application_config_set(TRAEFIK, {cfg_key: "foo.com"})

    new_cfg = juju.application_config_get(TRAEFIK)
    assert new_cfg.charm.get(cfg_key) == "foo.com"

    juju.application_config_set(TRAEFIK, {cfg_key: None})  # unset

    final_cfg = juju.application_config_get(TRAEFIK)
    assert final_cfg.charm.get(cfg_key) is None
