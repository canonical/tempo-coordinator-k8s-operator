output "app_name" {
  value = juju_application.tempo_coordinator.name
}

output "endpoints" {
  value = {
    certificates      = "certificates",
    grafana_dashboard = "grafana-dashboard",
    grafana_source    = "grafana-source",
    ingress           = "ingress",
    logging           = "logging",
    metrics_endpoint  = "metrics-endpoint",
    s3                = "s3",
    self_tracing      = "self-tracing",
    send-remote-write = "send-remote-write",
    tempo_cluster     = "tempo-cluster",
    tracing           = "tracing",
  }
}