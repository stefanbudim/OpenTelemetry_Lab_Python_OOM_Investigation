# OpenTelemetry OOM & Performance Investigation Reference

This document provides a technical reference for diagnosing Out-Of-Memory (OOM) and high CPU events in containerized Python applications on Podman and OpenShift.

---

## 1. Key Metrics Cheat Sheet

| Metric | Source | Role in OOM Investigation |
| :--- | :--- | :--- |
| `container_memory_working_set_bytes` | cAdvisor / OpenShift | **Primary metric used for OOMKill decisions.** Equals `memory.current - inactive_file`. |
| `process_memory_rss_bytes` | Python App (OpenTelemetry) | Resident RAM occupied by Python objects, heap, and native libraries. |
| `process_memory_vms_bytes` | Python App (OpenTelemetry) | Virtual address space allocated. Does not directly cause physical OOM. |
| `container_memory_cgroup_limit_bytes` | Container cgroups (`memory.max`) | Hard ceiling configured via `resources.limits.memory`. |
| `container_memory_cgroup_utilization_ratio` | Calculated in app / Prometheus | Ratio of working set to hard limit. Warn when $> 0.85$, Critical when $> 0.90$. |
| `process_cpu_utilization_percent` | Python App (OpenTelemetry) | CPU consumption percentage per core/thread. |
| `kube_pod_container_status_last_terminated_reason{reason="OOMKilled"}` | kube-state-metrics | Value `1` confirms the pod was terminated due to an OOMKill event. |

---

## 2. Tools & Ingestion Architecture

* **OTLP gRPC (`localhost:4317`)**: Non-HTTP binary protobuf protocol used to push metrics from Flask to the OpenTelemetry Collector.
* **OpenTelemetry Collector**: Buffers, limits memory, and exports metrics to Prometheus format (`:8889/metrics`).
* **Prometheus (`:9090`)**: Scrapes the collector and stores time-series data.
* **Grafana (`:3000`)**: Dashboards for memory headroom, RSS vs limits, and CPU load.

---

## 3. Useful Commands

```bash
# Check container memory statistics live
podman stats oom-flask-app --no-stream

# Verify if container exited due to OOM
podman inspect oom-flask-app --format 'Status: {{.State.Status}}, ExitCode: {{.State.ExitCode}}, OOMKilled: {{.State.OOMKilled}}'

# OpenShift pod events
oc describe pod <pod-name> | grep -E "OOMKilled|Exit Code"
```
