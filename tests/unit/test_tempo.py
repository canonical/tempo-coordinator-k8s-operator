import pytest

import tempo_config
from tempo import Tempo


@pytest.mark.parametrize(
    "protocols, use_tls, expected_config",
    (
        (
            (
                "otlp_grpc",
                "otlp_http",
                "zipkin",
                "tempo",
                "jaeger_http_thrift",
                "jaeger_grpc",
                "jaeger_thrift_http",
                "jaeger_thrift_http",
            ),
            False,
            {
                "jaeger": {
                    "protocols": {
                        "grpc": {"endpoint": "0.0.0.0:14250"},
                        "thrift_http": {"endpoint": "0.0.0.0:14268"},
                    }
                },
                "zipkin": {"endpoint": "0.0.0.0:9411"},
                "otlp": {
                    "protocols": {
                        "http": {"endpoint": "0.0.0.0:4318"},
                        "grpc": {"endpoint": "0.0.0.0:4317"},
                    }
                },
            },
        ),
        (
            ("otlp_http", "zipkin", "tempo", "jaeger_thrift_http"),
            False,
            {
                "jaeger": {
                    "protocols": {
                        "thrift_http": {"endpoint": "0.0.0.0:14268"},
                    }
                },
                "zipkin": {"endpoint": "0.0.0.0:9411"},
                "otlp": {"protocols": {"http": {"endpoint": "0.0.0.0:4318"}}},
            },
        ),
        (
            ("otlp_http", "zipkin", "tempo", "jaeger_thrift_http"),
            True,
            {
                "jaeger": {
                    "protocols": {
                        "thrift_http": {
                            "tls": {
                                "ca_file": "/usr/local/share/ca-certificates/ca.crt",
                                "cert_file": "/etc/worker/server.cert",
                                "key_file": "/etc/worker/private.key",
                            },
                            "endpoint": "0.0.0.0:14268",
                        },
                    }
                },
                "zipkin": {
                    "tls": {
                        "ca_file": "/usr/local/share/ca-certificates/ca.crt",
                        "cert_file": "/etc/worker/server.cert",
                        "key_file": "/etc/worker/private.key",
                    },
                    "endpoint": "0.0.0.0:9411",
                },
                "otlp": {
                    "protocols": {
                        "http": {
                            "tls": {
                                "ca_file": "/usr/local/share/ca-certificates/ca.crt",
                                "cert_file": "/etc/worker/server.cert",
                                "key_file": "/etc/worker/private.key",
                            },
                            "endpoint": "0.0.0.0:4318",
                        },
                    }
                },
            },
        ),
        ([], False, {}),
    ),
)
def test_tempo_distributor_config(protocols, use_tls, expected_config):
    assert (
        Tempo(None, 720)._build_distributor_config(protocols, use_tls).receivers == expected_config
    )


@pytest.mark.parametrize(
    "peers, expected_config",
    (
        (
            [],
            tempo_config.Memberlist(
                abort_if_cluster_join_fails=False, bind_port=7946, join_members=[]
            ),
        ),
        (
            ["peer1", "peer2"],
            tempo_config.Memberlist(
                abort_if_cluster_join_fails=False,
                bind_port=7946,
                join_members=["peer1:7946", "peer2:7946"],
            ),
        ),
    ),
)
def test_tempo_memberlist_config(peers, expected_config):
    assert Tempo(None, 720)._build_memberlist_config(peers) == expected_config


@pytest.mark.parametrize(
    "addresses, expected_replication",
    (
        (
            {"querier": {"addr1"}, "ingester": {"addr1", "addr2", "addr3"}},
            3,
        ),
        (
            {"querier": {"addr1"}},
            1,
        ),
        (
            {"ingester": {"addr2"}, "querier": {"addr1"}},
            1,
        ),
    ),
)
def test_tempo_ingester_config(addresses, expected_replication):
    assert (
        Tempo(None, 720)._build_ingester_config(addresses).lifecycler.ring.replication_factor
        == expected_replication
    )
