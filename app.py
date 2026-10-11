#!/usr/bin/env python3
"""
Flask Application with OpenTelemetry Metrics and OOM / CPU Stress Testing.
Designed for Podman & OpenShift environments to investigate container OOM events.
"""

import os
import sys
import time
import math
import hashlib
import gc
import logging
import threading
from typing import Iterable, List, Optional, Dict, Any

from flask import Flask, render_template, request, jsonify
import psutil

# OpenTelemetry imports
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.resources import Resource, SERVICE_NAME, SERVICE_VERSION, DEPLOYMENT_ENVIRONMENT
from opentelemetry.instrumentation.flask import FlaskInstrumentor
from opentelemetry.metrics import CallbackOptions, Observation

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("oom-flask-app")

app = Flask(__name__)

# Global configuration & state
DEFAULT_OTEL_COLLECTOR = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "localhost:4317")
OTEL_COLLECTOR_INSECURE = os.getenv("OTEL_EXPORTER_OTLP_INSECURE", "true").lower() in ("true", "1", "yes")
current_collector_endpoint = DEFAULT_OTEL_COLLECTOR

# Stress test state management
stress_lock = threading.Lock()
stress_memory_blocks: List[bytearray] = []
cpu_stress_active = False
cpu_stress_threads: List[threading.Thread] = []
active_stress_info: Dict[str, Any] = {
    "running": False,
    "memory_target_mb": 0,
    "memory_allocated_mb": 0,
    "duration_seconds": 0,
    "start_time": None,
    "cpu_workers": 0,
    "leak_permanently": False,
}

# --- Cgroup Memory Inspector ---
def get_cgroup_memory_info() -> Dict[str, Optional[int]]:
    """
    Inspects Linux cgroups (v2 and v1) to retrieve container memory limits and usage.
    This reflects the exact metrics OpenShift and Linux kernel OOM killer use!
    """
    usage_bytes = None
    limit_bytes = None
    version = "unknown"

    # Check cgroup v2 (default in modern Linux & OpenShift 4.12+)
    cg2_current = "/sys/fs/cgroup/memory.current"
    cg2_max = "/sys/fs/cgroup/memory.max"

    if os.path.exists(cg2_current) and os.path.exists(cg2_max):
        version = "v2"
        try:
            with open(cg2_current, "r") as f:
                usage_bytes = int(f.read().strip())
            with open(cg2_max, "r") as f:
                val = f.read().strip()
                limit_bytes = int(val) if val != "max" else None
        except Exception as e:
            logger.debug("Failed reading cgroup v2: %s", e)

    # Check cgroup v1 fallback
    if usage_bytes is None:
        cg1_usage = "/sys/fs/cgroup/memory/memory.usage_in_bytes"
        cg1_limit = "/sys/fs/cgroup/memory/memory.limit_in_bytes"
        if os.path.exists(cg1_usage) and os.path.exists(cg1_limit):
            version = "v1"
            try:
                with open(cg1_usage, "r") as f:
                    usage_bytes = int(f.read().strip())
                with open(cg1_limit, "r") as f:
                    val = int(f.read().strip())
                    # In cgroup v1, unbounded limit is close to 2^63 - 4096 (very large int)
                    limit_bytes = val if val < (1 << 60) else None
            except Exception as e:
                logger.debug("Failed reading cgroup v1: %s", e)

    # Host fallback if not in a container with limits
    if usage_bytes is None:
        mem = psutil.virtual_memory()
        usage_bytes = mem.used
        limit_bytes = mem.total
        version = "host"
    elif limit_bytes is None:
        mem = psutil.virtual_memory()
        limit_bytes = mem.total

    return {
        "version": version,
        "usage_bytes": usage_bytes,
        "limit_bytes": limit_bytes,
    }

# --- OpenTelemetry Metric Setup ---
resource = Resource.create({
    SERVICE_NAME: "oom-metrics-flask-app",
    SERVICE_VERSION: "1.0.0",
    DEPLOYMENT_ENVIRONMENT: os.getenv("APP_ENV", "production"),
})

meter_provider: Optional[MeterProvider] = None
metric_reader: Optional[PeriodicExportingMetricReader] = None

def init_opentelemetry(endpoint: str):
    """Initializes or re-initializes OpenTelemetry with OTLP gRPC exporter."""
    global meter_provider, metric_reader, current_collector_endpoint
    try:
        current_collector_endpoint = endpoint
        # Normalizing endpoint for gRPC
        # e.g., if passed localhost:4317 or http://localhost:4317
        clean_endpoint = endpoint
        if clean_endpoint.startswith("http://"):
            clean_endpoint = clean_endpoint[len("http://"):]
        elif clean_endpoint.startswith("https://"):
            clean_endpoint = clean_endpoint[len("https://"):]

        logger.info(f"Configuring OTLP gRPC Metric Exporter to: {clean_endpoint} (insecure={OTEL_COLLECTOR_INSECURE})")

        exporter = OTLPMetricExporter(
            endpoint=clean_endpoint,
            insecure=OTEL_COLLECTOR_INSECURE,
            timeout=5,
        )

        # Export every 5 seconds so metrics appear quickly in dashboards
        metric_reader = PeriodicExportingMetricReader(exporter, export_interval_millis=5000)
        provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
        metrics.set_meter_provider(provider)
        meter_provider = provider
        logger.info("OpenTelemetry gRPC Metric Exporter initialized successfully.")
    except Exception as e:
        logger.error(f"Error setting up OpenTelemetry exporter: {e}")

# Initial setup
init_opentelemetry(DEFAULT_OTEL_COLLECTOR)
meter = metrics.get_meter("oom-flask-app", "1.0.0")

# Register Observable Metrics Callbacks
def observe_process_rss(options: CallbackOptions) -> Iterable[Observation]:
    process = psutil.Process()
    rss = process.memory_info().rss
    yield Observation(rss, {"metric.type": "process_rss"})

def observe_process_vms(options: CallbackOptions) -> Iterable[Observation]:
    process = psutil.Process()
    vms = process.memory_info().vms
    yield Observation(vms, {"metric.type": "process_vms"})

def observe_container_cgroup_usage(options: CallbackOptions) -> Iterable[Observation]:
    info = get_cgroup_memory_info()
    if info["usage_bytes"] is not None:
        yield Observation(info["usage_bytes"], {"cgroup.version": info["version"]})

def observe_container_cgroup_limit(options: CallbackOptions) -> Iterable[Observation]:
    info = get_cgroup_memory_info()
    if info["limit_bytes"] is not None:
        yield Observation(info["limit_bytes"], {"cgroup.version": info["version"]})

def observe_container_cgroup_utilization(options: CallbackOptions) -> Iterable[Observation]:
    info = get_cgroup_memory_info()
    if info["usage_bytes"] is not None and info["limit_bytes"]:
        ratio = info["usage_bytes"] / float(info["limit_bytes"])
        yield Observation(ratio, {"cgroup.version": info["version"]})

def observe_process_cpu_percent(options: CallbackOptions) -> Iterable[Observation]:
    process = psutil.Process()
    cpu_percent = process.cpu_percent(interval=None)
    yield Observation(cpu_percent, {"metric.type": "cpu_utilization"})

def observe_stress_memory_allocated(options: CallbackOptions) -> Iterable[Observation]:
    with stress_lock:
        allocated = active_stress_info["memory_allocated_mb"]
    yield Observation(allocated, {"metric.type": "stress_memory_allocated_mb"})

# Register OpenTelemetry Observable Gauges
meter.create_observable_gauge(
    name="process_memory_rss_bytes",
    callbacks=[observe_process_rss],
    description="Process Resident Set Size (RSS) memory in bytes",
    unit="By",
)

meter.create_observable_gauge(
    name="process_memory_vms_bytes",
    callbacks=[observe_process_vms],
    description="Process Virtual Memory Size (VMS) in bytes",
    unit="By",
)

meter.create_observable_gauge(
    name="container_memory_cgroup_usage_bytes",
    callbacks=[observe_container_cgroup_usage],
    description="Container cgroup memory current usage in bytes",
    unit="By",
)

meter.create_observable_gauge(
    name="container_memory_cgroup_limit_bytes",
    callbacks=[observe_container_cgroup_limit],
    description="Container cgroup memory maximum limit in bytes",
    unit="By",
)

meter.create_observable_gauge(
    name="container_memory_cgroup_utilization_ratio",
    callbacks=[observe_container_cgroup_utilization],
    description="Ratio of container memory usage to cgroup limit (1.0 = OOM limit)",
    unit="1",
)

meter.create_observable_gauge(
    name="process_cpu_utilization_percent",
    callbacks=[observe_process_cpu_percent],
    description="Process CPU utilization percentage",
    unit="percent",
)

meter.create_observable_gauge(
    name="app_stress_active_memory_mb",
    callbacks=[observe_stress_memory_allocated],
    description="Active memory load allocated by stress testing in megabytes",
    unit="MB",
)

# Counter metrics
request_counter = meter.create_counter(
    name="app_http_requests_total",
    description="Total count of HTTP requests served by the Flask application",
    unit="1",
)

oom_test_counter = meter.create_counter(
    name="app_oom_tests_triggered_total",
    description="Total count of OOM stress test runs initiated",
    unit="1",
)

# Instrument Flask
FlaskInstrumentor().instrument_app(app)

# --- CPU Stress Worker ---
def _cpu_worker(stop_event: threading.Event):
    """Spins a busy CPU loop calculating SHA256 hashes and sqrt."""
    while not stop_event.is_set():
        # High CPU compute cycle
        val = 0
        for i in range(50000):
            val += math.sqrt(i)
        _ = hashlib.sha256(str(val).encode()).hexdigest()

# --- Stress Execution Routine ---
def execute_stress(load_mb: int, duration_sec: int, cpu_workers: int, leak_permanently: bool):
    global cpu_stress_active, cpu_stress_threads
    stop_cpu_event = threading.Event()

    with stress_lock:
        active_stress_info["running"] = True
        active_stress_info["memory_target_mb"] = load_mb
        active_stress_info["duration_seconds"] = duration_sec
        active_stress_info["cpu_workers"] = cpu_workers
        active_stress_info["leak_permanently"] = leak_permanently
        active_stress_info["start_time"] = time.time()

    logger.info(f"Starting stress test: Memory={load_mb}MB, CPU workers={cpu_workers}, Duration={duration_sec}s, Leak={leak_permanently}")

    # Launch CPU workers
    cpu_threads = []
    if cpu_workers > 0:
        cpu_stress_active = True
        for i in range(cpu_workers):
            t = threading.Thread(target=_cpu_worker, args=(stop_cpu_event,), daemon=True, name=f"cpu-stress-{i}")
            t.start()
            cpu_threads.append(t)

    # Allocate memory incrementally (10 MB chunks)
    # We populate chunks with byte value 0xFF so Linux forces physical page allocation (RSS)
    chunk_size = 10 * 1024 * 1024  # 10 MB
    chunks_to_allocate = load_mb // 10
    remainder_mb = load_mb % 10

    try:
        for i in range(chunks_to_allocate):
            if not active_stress_info["running"]:
                break
            # Allocate 10MB chunk with real byte values
            block = bytearray(b'\xff' * chunk_size)
            with stress_lock:
                stress_memory_blocks.append(block)
                active_stress_info["memory_allocated_mb"] = len(stress_memory_blocks) * 10
            time.sleep(0.05)  # slight stagger for smooth metric ramp up

        if remainder_mb > 0 and active_stress_info["running"]:
            block = bytearray(b'\xff' * (remainder_mb * 1024 * 1024))
            with stress_lock:
                stress_memory_blocks.append(block)
                active_stress_info["memory_allocated_mb"] = (len(stress_memory_blocks) - 1) * 10 + remainder_mb

        logger.info(f"Target memory reached: {active_stress_info['memory_allocated_mb']}MB. Holding for {duration_sec}s...")

        # Hold allocation for the requested duration
        elapsed = 0
        while elapsed < duration_sec and active_stress_info["running"]:
            time.sleep(1)
            elapsed += 1

    except MemoryError:
        logger.critical("Python MemoryError caught! Process was near OOM.")
    finally:
        # Stop CPU workers
        stop_cpu_event.set()
        for t in cpu_threads:
            t.join(timeout=1.0)
        cpu_stress_active = False

        # Release memory if not configured to leak permanently
        if not leak_permanently:
            with stress_lock:
                stress_memory_blocks.clear()
                active_stress_info["memory_allocated_mb"] = 0
                active_stress_info["running"] = False
            gc.collect()
            logger.info("Stress duration elapsed. Memory released and GC triggered.")
        else:
            with stress_lock:
                active_stress_info["running"] = False
            logger.warning(f"Stress test finished, but {load_mb}MB retained permanently as requested.")

# --- Web & API Endpoints ---
@app.before_request
def count_requests():
    if request.path != "/api/status":
        request_counter.add(1, {"endpoint": request.path})

@app.route("/")
def index():
    return render_template("index.html", default_collector=current_collector_endpoint)

@app.route("/api/status")
def status():
    """Returns current system, cgroup, memory, and stress test statistics."""
    process = psutil.Process()
    mem_info = process.memory_info()
    cgroup = get_cgroup_memory_info()

    limit_bytes = cgroup["limit_bytes"] or psutil.virtual_memory().total
    cgroup_usage = cgroup["usage_bytes"] or mem_info.rss
    utilization_pct = round((cgroup_usage / limit_bytes) * 100, 2) if limit_bytes else 0

    with stress_lock:
        stress_status = dict(active_stress_info)

    return jsonify({
        "timestamp": time.time(),
        "process": {
            "pid": process.pid,
            "rss_mb": round(mem_info.rss / (1024 * 1024), 2),
            "vms_mb": round(mem_info.vms / (1024 * 1024), 2),
            "cpu_percent": process.cpu_percent(interval=None),
            "threads": process.num_threads(),
        },
        "container_cgroup": {
            "version": cgroup["version"],
            "usage_mb": round(cgroup_usage / (1024 * 1024), 2),
            "limit_mb": round(limit_bytes / (1024 * 1024), 2) if limit_bytes else "unlimited",
            "headroom_mb": round((limit_bytes - cgroup_usage) / (1024 * 1024), 2) if limit_bytes else "N/A",
            "utilization_pct": utilization_pct,
        },
        "stress": stress_status,
        "collector_endpoint": current_collector_endpoint,
        "insecure_grpc": OTEL_COLLECTOR_INSECURE,
    })

@app.route("/api/stress/start", methods=["POST"])
def start_stress():
    data = request.get_json() or {}
    load_mb = int(data.get("load_mb", 200))
    duration_sec = int(data.get("duration_sec", 30))
    cpu_workers = int(data.get("cpu_workers", 2))
    leak_permanently = bool(data.get("leak_permanently", False))

    with stress_lock:
        if active_stress_info["running"]:
            return jsonify({"status": "error", "message": "A stress test is already running."}), 409

    oom_test_counter.add(1, {"load_mb": str(load_mb), "cpu_workers": str(cpu_workers)})

    t = threading.Thread(
        target=execute_stress,
        args=(load_mb, duration_sec, cpu_workers, leak_permanently),
        daemon=True,
        name="stress-controller",
    )
    t.start()

    return jsonify({
        "status": "started",
        "load_mb": load_mb,
        "duration_sec": duration_sec,
        "cpu_workers": cpu_workers,
        "leak_permanently": leak_permanently,
    })

@app.route("/api/stress/stop", methods=["POST"])
def stop_stress():
    with stress_lock:
        active_stress_info["running"] = False
        stress_memory_blocks.clear()
        active_stress_info["memory_allocated_mb"] = 0
    gc.collect()
    logger.info("Manual stop requested: stress terminated and memory freed.")
    return jsonify({"status": "stopped", "message": "Stress test stopped and memory released."})

@app.route("/api/config/collector", methods=["POST"])
def config_collector():
    data = request.get_json() or {}
    endpoint = data.get("endpoint", DEFAULT_OTEL_COLLECTOR).strip()
    if not endpoint:
        endpoint = DEFAULT_OTEL_COLLECTOR
    init_opentelemetry(endpoint)
    return jsonify({"status": "updated", "endpoint": current_collector_endpoint})

@app.route("/healthz")
def healthz():
    return jsonify({"status": "healthy"})

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    host = os.getenv("HOST", "0.0.0.0")
    logger.info(f"Starting Flask App on {host}:{port}")
    app.run(host=host, port=port, debug=False, threaded=True)
