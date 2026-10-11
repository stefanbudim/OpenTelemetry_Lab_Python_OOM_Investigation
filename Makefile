SHELL := /bin/bash
DOCKER_USER ?= stefanbudim
IMAGE_BASE ?= oom-flask-app
IMAGE_TAG ?= latest
IMAGE_NAME ?= $(IMAGE_BASE):$(IMAGE_TAG)
DOCKER_IMAGE ?= $(DOCKER_USER)/$(IMAGE_BASE):$(IMAGE_TAG)
CONTAINER_NAME ?= oom-flask-app
MEMORY_LIMIT ?= 256m
CPU_LIMIT ?= 1.0
PORT ?= 5000
CONTAINER_ENGINE ?= $(shell if docker info >/dev/null 2>&1; then echo "docker"; else echo "podman"; fi)
COMPOSE_CMD ?= $(shell if docker compose version >/dev/null 2>&1; then echo "docker compose"; elif which podman-compose >/dev/null 2>&1; then echo "podman-compose"; else echo "docker-compose"; fi)
OC_CMD ?= $(shell which oc 2>/dev/null || which kubectl 2>/dev/null || echo "kubectl")

.PHONY: help venv install run-local test-local-stress build-container run-container \
	test-container-oom up-prometheus up-stack down-stack deploy-openshift test-openshift-oom \
	docker-login docker-tag publish-image publish push clean

help:
	@echo "=========================================================================="
	@echo "OpenTelemetry OOM & Performance Investigator - Workflow Commands"
	@echo "=========================================================================="
	@echo "1. Local Development:"
	@echo "   make venv                 - Create Python virtual environment (.venv)"
	@echo "   make install              - Install Python dependencies"
	@echo "   make run-local            - Run Flask app locally on http://localhost:5000"
	@echo "   make test-local-stress    - Trigger 150MB memory and 2 CPU load test via curl"
	@echo ""
	@echo "2. Container Testing & Publishing:"
	@echo "   make build-container      - Build container image ($(IMAGE_NAME))"
	@echo "   make run-container        - Run container locally with $(MEMORY_LIMIT) limit"
	@echo "   make test-container-oom   - Force OOM kill in container and inspect exit code 137"
	@echo "   make docker-login         - Log in to Docker Hub as $(DOCKER_USER)"
	@echo "   make publish-image        - Build, tag, and publish to Docker Hub ($(DOCKER_IMAGE))"
	@echo ""
	@echo "3. Monitoring Stack (Prometheus & Grafana):"
	@echo "   make up-prometheus        - Start OTel Collector and Prometheus"
	@echo "   make up-stack             - Start full stack: App + Collector + Prometheus + Grafana"
	@echo "   make down-stack           - Stop all containers"
	@echo ""
	@echo "4. OpenShift Deployment & Testing:"
	@echo "   make deploy-openshift     - Deploy OTel Collector & App to OpenShift/K8s"
	@echo "   make test-openshift-oom   - Trigger OOM in OpenShift and inspect pod termination"
	@echo "   make clean                - Clean temporary files and caches"
	@echo "=========================================================================="

# -----------------------------------------------------------------------------
# 1. Local Python Development
# -----------------------------------------------------------------------------
venv:
	@test -d .venv || python3 -m venv .venv
	@echo "Virtual environment ready at .venv. Activate with: source .venv/bin/activate"

install: venv
	@source .venv/bin/activate && pip install --upgrade pip && pip install -r requirements.txt

run-local: install
	@echo "Starting Flask app locally on http://127.0.0.1:$(PORT)..."
	@source .venv/bin/activate && \
	export OTEL_EXPORTER_OTLP_ENDPOINT="localhost:4317" && \
	export OTEL_EXPORTER_OTLP_INSECURE="true" && \
	python3 app.py

test-local-stress:
	@echo "Triggering local stress test (Load: 150MB, Duration: 15s, CPU workers: 2)..."
	@curl -s -X POST http://localhost:$(PORT)/api/stress/start \
		-H "Content-Type: application/json" \
		-d '{"load_mb": 150, "duration_sec": 15, "cpu_workers": 2, "leak_permanently": false}' | jq . || true
	@echo ""
	@echo "Current status:"
	@curl -s http://localhost:$(PORT)/api/status | jq . || true

# -----------------------------------------------------------------------------
# 2. Podman Container Testing Locally
# -----------------------------------------------------------------------------
build-container:
	@echo "Building container with $(CONTAINER_ENGINE)..."
	$(CONTAINER_ENGINE) build -t $(IMAGE_NAME) -f Containerfile .

run-container: build-container
	@echo "Running container in $(CONTAINER_ENGINE) with memory limit: $(MEMORY_LIMIT)..."
	-$(CONTAINER_ENGINE) rm -f $(CONTAINER_NAME) 2>/dev/null || true
	$(CONTAINER_ENGINE) run -d --name $(CONTAINER_NAME) \
		-p $(PORT):5000 \
		--memory=$(MEMORY_LIMIT) \
		--memory-swap=$(MEMORY_LIMIT) \
		--cpus=$(CPU_LIMIT) \
		-e OTEL_EXPORTER_OTLP_ENDPOINT="host.containers.internal:4317" \
		-e OTEL_EXPORTER_OTLP_INSECURE="true" \
		$(IMAGE_NAME)
	@echo "Container running! Access UI at http://localhost:$(PORT)"
	@echo "Check memory stats with: $(CONTAINER_ENGINE) stats $(CONTAINER_NAME) --no-stream"

test-container-oom:
	@echo "Triggering 350MB allocation against $(MEMORY_LIMIT) container limit to force OOM..."
	-curl -s -X POST http://localhost:$(PORT)/api/stress/start \
		-H "Content-Type: application/json" \
		-d '{"load_mb": 350, "duration_sec": 30, "cpu_workers": 2, "leak_permanently": true}' || true
	@echo "Waiting for Linux kernel cgroup OOM Killer to strike..."
	@sleep 4
	@echo "Inspecting container state:"
	@$(CONTAINER_ENGINE) inspect $(CONTAINER_NAME) --format 'Status: {{.State.Status}}, ExitCode: {{.State.ExitCode}}, OOMKilled: {{.State.OOMKilled}}' || true
	@echo "ExitCode 137 and OOMKilled=true confirms kernel killed process due to memory limit breach!"

# -----------------------------------------------------------------------------
# 2b. Docker Hub Registry Publishing (https://hub.docker.com/u/stefanbudim)
# -----------------------------------------------------------------------------
docker-login:
	@echo "Logging in to Docker Hub as $(DOCKER_USER)..."
	@$(CONTAINER_ENGINE) login docker.io -u $(DOCKER_USER)

docker-tag: build-container
	@echo "Tagging image $(IMAGE_NAME) -> $(DOCKER_IMAGE)..."
	$(CONTAINER_ENGINE) tag $(IMAGE_NAME) $(DOCKER_IMAGE)

publish-image: docker-tag
	@echo "Pushing $(DOCKER_IMAGE) to Docker Hub (https://hub.docker.com/u/$(DOCKER_USER))..."
	$(CONTAINER_ENGINE) push $(DOCKER_IMAGE)
	@echo "Successfully published: https://hub.docker.com/r/$(DOCKER_USER)/$(IMAGE_BASE)"

publish: publish-image
push: publish-image

# -----------------------------------------------------------------------------
# 3. Prometheus & Grafana Monitoring Stack
# -----------------------------------------------------------------------------
up-prometheus:
	@echo "Starting OTel Collector and Prometheus..."
	-$(CONTAINER_ENGINE) rm -f $(CONTAINER_NAME) 2>/dev/null || true
	$(COMPOSE_CMD) up -d otel-collector prometheus
	@echo "OTel Collector gRPC listening on :4317"
	@echo "Prometheus listening on http://localhost:9090"

up-stack:
	@echo "Starting full observability stack (App + OTel Collector + Prometheus + Grafana)..."
	-$(CONTAINER_ENGINE) rm -f $(CONTAINER_NAME) 2>/dev/null || true
	$(COMPOSE_CMD) up -d
	@echo "=========================================================="
	@echo "Flask App UI:      http://localhost:5000"
	@echo "OTel Collector:    gRPC localhost:4317 (metrics exporter: :8889)"
	@echo "Prometheus:        http://localhost:9090"
	@echo "Grafana Dashboard: http://localhost:3000 (auto-login admin)"
	@echo "=========================================================="

down-stack:
	@echo "Stopping stack..."
	$(COMPOSE_CMD) down -v

# -----------------------------------------------------------------------------
# 4. OpenShift Testing
# -----------------------------------------------------------------------------
deploy-openshift:
	@echo "Deploying to OpenShift using $(OC_CMD)..."
	$(OC_CMD) apply -f openshift/01-otel-collector.yaml
	$(OC_CMD) apply -f openshift/02-app.yaml
	@echo "Deployment initiated. Waiting for rollout..."
	-$(OC_CMD) rollout status deployment/oom-flask-app --timeout=60s || true
	@echo "OpenShift Route URL:"
	-$(OC_CMD) get route oom-flask-app -o jsonpath='{.spec.host}' && echo "" || true

test-openshift-oom:
	@echo "Inspecting OpenShift pod events and OOM status..."
	@echo "1. Recent Pods:"
	$(OC_CMD) get pods -l app=oom-flask-app
	@echo ""
	@echo "2. Pod Termination Reasons (look for Reason: OOMKilled, Exit Code: 137):"
	-$(OC_CMD) get pods -l app=oom-flask-app -o jsonpath='{range .items[*]}{.metadata.name}{": "}{.status.containerStatuses[*].lastState.terminated.reason}{" (exit "}{.status.containerStatuses[*].lastState.terminated.exitCode}{")\n"}{end}'
	@echo ""
	@echo "3. Kernel / Kubelet OOM Events:"
	-$(OC_CMD) get events --field-selector reason=OOMKilling || $(OC_CMD) get events | grep -i oom || true

clean:
	@rm -rf __pycache__ .pytest_cache
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@echo "Cleaned up cache."
