# Prompt to create this Project
example python flask app uses opentelemetry.

sometimes OOM occure in podman container running in openshift.

What are metrics to investigate the OOM?

What tools to use to analyse the opentelemetry metrics?

provide UI with
- OpenTelemetry Collector default localhost, default non http protocal use the other protocal
- for a time genereate high cpu and high memory. time and load should be input parameters

dev workflow
- test locally
- test podman container locally
- test podman container in openshift
- test with Prometheus docker
- test with Prometheus and Grafana docker
- Makefile


At the end of the task, create a clean markdown file containing a list or code block summarizing every single terminal command and file creation you executed during this session.

# Model: Gemini 3.6 Flash







# Which metrics help investigate OOM?
Don't look only at total memory. You want to see whether memory rises gradually, spikes during requests, or approaches the container's configured limit.
Metric	Why it matters
Process resident memory (RSS)	Actual physical memory resident in the Python process; a key signal for growth
Python allocated memory	Helps identify growing Python-managed allocations
Container working set memory	Shows memory usage at container level, including more than just Python heap allocations
Container memory limit	The threshold that can trigger an OOM kill
Container memory usage / limit	Shows how close you are to the limit
CPU usage and throttling	Helps correlate memory spikes with load and constrained processing
Request rate and concurrency	Identifies whether higher load or simultaneous requests trigger growth
Request duration and errors	Shows application behavior around a memory spike
Garbage collection activity	Can help reveal allocation pressure or objects being retained
Restart count and OOMKilled status	Confirms that a container was killed and helps correlate the incident