output "app_name" {
  value = juju_application.tempo_coordinator.name
}

output "endpoints" {
  value = {
    # Requires
    certificates      = "certificates",
    ingress           = "ingress",
    logging           = "logging",
    s3                = "s3",
    self_tracing      = "self-tracing",
    send-remote-write = "send-remote-write",
    
    # Provides
    grafana_dashboard = "grafana-dashboard",
    grafana_source    = "grafana-source",
    metrics_endpoint  = "metrics-endpoint",
    tempo_cluster     = "tempo-cluster",
    tracing           = "tracing",
  }
}