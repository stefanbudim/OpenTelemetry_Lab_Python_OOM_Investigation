# Session Execution Log: File Creations and Terminal Commands

This document summarizes every file creation and terminal command executed during this session to set up, instrument, test, and containerize the OpenTelemetry Flask application for OOM and CPU stress testing.

---

## 1. Files Created and Modified

| # | File Path | Type | Description |
|---|---|---|---|
| 1 | `requirements.txt` | Created | Python dependencies (Flask, OpenTelemetry SDK, OTLP gRPC exporter, psutil). |
| 2 | `app.py` | Created | Flask application with OTLP gRPC metric exporter, cgroups v1/v2 reader, and CPU/memory stress engine. |
| 3 | `templates/index.html` | Created / Edited | Web UI dashboard for live metrics, collector configuration, and stress testing (fixed Jinja escaping for Podman format). |
| 4 | `otel-collector-config.yaml` | Created | OpenTelemetry Collector configuration (OTLP gRPC `:4317` to Prometheus exporter `:8889`). |
| 5 | `prometheus/prometheus.yml` | Created | Prometheus server configuration scraping the OTel collector. |
| 6 | `grafana/provisioning/datasources/prometheus.yml` | Created | Auto-provisioned Grafana datasource for Prometheus. |
| 7 | `grafana/provisioning/dashboards/dashboard.yml` | Created | Grafana dashboard provisioning provider. |
| 8 | `grafana/provisioning/dashboards/oom-dashboard.json` | Created | Pre-configured dashboard for OOM headroom, cgroup limits, RSS, and CPU utilization. |
| 9 | `Containerfile` | Created | OpenShift and Podman non-root container definition (`USER 1001` + group 0 permissions). |
| 10 | `compose.yaml` | Created | Multi-container stack (App with 256M limit, OTel Collector, Prometheus, Grafana). |
| 11 | `openshift/01-otel-collector.yaml` | Created | OpenShift ConfigMap, Deployment, and Service for OpenTelemetry Collector. |
| 12 | `openshift/02-app.yaml` | Created | OpenShift Deployment (`limits.memory: 256Mi`), Service, and Route for the Flask app. |
| 13 | `Makefile` | Created / Edited | Automated targets for local venv, container build, OOM simulation, monitoring stack, and OpenShift. |
| 14 | `.dockerignore` | Created | Excluded `.venv`, caches, and git files from container builds. |
| 15 | `README.md` | Created | Comprehensive guide on OOM metrics, OpenTelemetry tooling, and dev workflows. |
| 16 | `.gitignore` | Created | Ignores temporary/build files and explicitly tracks the `doc/` directory (`!doc/`). |
| 17 | `doc/oom-investigation-guide.md` | Created | Metric reference guide and diagnostic commands inside the doc directory. |
| 18 | `doc/.gitkeep` | Created | Ensures the `doc/` folder is preserved in version control. |
| 19 | `doc/session-execution-log.md` | Created | This session summary document. |

---

## 2. Terminal Commands Executed

```bash
# --- Initial Environment Inspection ---
ls -la
python3 --version && podman --version
which podman-compose docker-compose oc kubectl || true

# --- Project Directory Setup ---
mkdir -p templates static prometheus grafana/provisioning/datasources grafana/provisioning/dashboards openshift

# --- Virtual Environment Setup & Dependencies ---
python3 -m venv .venv && source .venv/bin/activate && pip install --upgrade pip && pip install -r requirements.txt

# --- Symlinks for Compatibility ---
ln -sf Containerfile Dockerfile
ln -sf compose.yaml docker-compose.yml
ln -sf .dockerignore .containerignore

# --- Local Python & Endpoint Testing ---
source .venv/bin/activate && python3 -c "import app; print('App module loaded successfully!')"

source .venv/bin/activate && python3 -c "
from app import app
client = app.test_client()
res = client.get('/api/status')
print('Status code:', res.status_code)
print('JSON:', res.get_json())
res_ui = client.get('/'); print('UI status code:', res_ui.status_code)
"

source .venv/bin/activate && python3 -c "
from app import app
client = app.test_client()
res_ui = client.get('/')
print('UI status code:', res_ui.status_code)
assert res_ui.status_code == 200
print('UI renders perfectly!')
"

source .venv/bin/activate && python3 -c "
import time
from app import app
client = app.test_client()
res = client.post('/api/stress/start', json={'load_mb': 50, 'duration_sec': 3, 'cpu_workers': 1})
print('Start response:', res.status_code, res.get_json())
assert res.status_code == 200
time.sleep(1.5)
status1 = client.get('/api/status').get_json()
print('Stress active memory MB:', status1['stress']['memory_allocated_mb'])
print('CPU %:', status1['process']['cpu_percent'])
assert status1['stress']['running'] is True
res_stop = client.post('/api/stress/stop')
print('Stop response:', res_stop.status_code, res_stop.get_json())
time.sleep(0.5)
status2 = client.get('/api/status').get_json()
print('Post-stop allocated MB:', status2['stress']['memory_allocated_mb'])
assert status2['stress']['running'] is False
print('Stress test test passed successfully!')
"

# --- Makefile & Container Engine Validation ---
make help
make build-container
cat /etc/subuid /etc/subgid 2>/dev/null || id
id && whoami
docker info 2>&1 | head -n 15

# --- Container Image Build ---
docker build -t oom-flask-app:latest -f Containerfile .

# --- Standalone Container Run with 256MB Limit & Strict Swap ---
make run-container
curl -s http://localhost:5000/api/status | jq .

# --- Container OOMKill Simulation & Inspection (Exit Code 137) ---
make test-container-oom
docker inspect oom-flask-app --format 'Status: {{.State.Status}}, ExitCode: {{.State.ExitCode}}, OOMKilled: {{.State.OOMKilled}}'
curl -s http://localhost:5000/api/status | jq .
make run-container && sleep 2 && make test-container-oom
sleep 2 && docker inspect oom-flask-app --format 'Status: {{.State.Status}}, ExitCode: {{.State.ExitCode}}, OOMKilled: {{.State.OOMKilled}}'

# --- Full Monitoring Stack (App + OTel Collector + Prometheus + Grafana) ---
make up-stack
curl -s http://localhost:5000/api/status | jq .
curl -s http://localhost:8889/metrics | grep otel_
curl -s http://localhost:9090/api/v1/targets | jq .data.activeTargets[0].health
curl -s http://localhost:3000/api/health | jq .

# --- Stress Test Execution in Compose Stack ---
curl -s -X POST http://localhost:5000/api/stress/start \
  -H "Content-Type: application/json" \
  -d '{"load_mb": 100, "duration_sec": 10, "cpu_workers": 2, "leak_permanently": false}' | jq .
curl -s http://localhost:5000/api/status | jq .
sleep 6 && curl -s http://localhost:5000/api/status | jq .

# --- Directory & Git Configuration Checks ---
ls -la
ls -la doc
git status || true
ls -la doc && cat .gitignore | tail -n 15

# --- Docker Hub Publishing ---
make help
make docker-tag
docker images | grep stefanbudim
# make docker-login
# make publish-image (or: make push)
```
