## Architecture & Protocol Details

```
+-----------------------------------------------------------+
| Python Flask App (Podman / OpenShift)                     |
|                                                           |
| - Web UI for Memory & CPU Stress Generation               |
| - Cgroup v1/v2 Inspector (/sys/fs/cgroup)                 |
| - OpenTelemetry Metric Exporter (OTLP / gRPC, Port 4317)  |
+-----------------------------------------------------------+
                             |
             OTLP / gRPC (HTTP/2 binary protobuf)
             Default Non-HTTP Protocol (Port 4317)
                             v
+-----------------------------------------------------------+
| OpenTelemetry Collector (Contrib)                         |
| - Receiver: OTLP gRPC (:4317)                             |
| - Processor: memory_limiter, batch                        |
| - Exporter: Prometheus (:8889), Debug (stdout)            |
+-----------------------------------------------------------+
                             |
                   Prometheus Scrape (:8889)
                             v
+-----------------------------------------------------------+
| Prometheus Server (:9090)                                 |
| - Scrapes otel_container_memory_cgroup_usage_bytes        |
| - Scrapes otel_process_memory_rss_bytes                   |
| - Scrapes otel_process_cpu_utilization_percent            |
+-----------------------------------------------------------+
                             |
                      Data Source (PromQL)
                             v
+-----------------------------------------------------------+
| Grafana Dashboard (:3000)                                 |
| - Headroom before Kernel OOMKill                          |
| - RSS vs Hard Cgroup Limit Time Series                    |
+-----------------------------------------------------------+
```
