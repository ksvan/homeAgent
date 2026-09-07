---
name: unifi-prometheus-network
description: Diagnose, measure, and report household UniFi network and Wi-Fi performance using the connected read-only Prometheus MCP tools, live Unpoller metrics, and Blackbox Exporter probes. Use for slow or unreliable connectivity, Wi-Fi coverage, client experience, access-point or gateway health, WAN quality, capacity, endpoint reachability, and evidence-based network status reports.
---

# UniFi Network Operations

Use the Prometheus MCP tools to investigate the household's live UniFi and
Wi-Fi telemetry exported by Unpoller, plus Blackbox Exporter reachability and
path telemetry. This skill is generic: discover the actual metric names,
labels, topology, probe targets, and baselines from the connected Prometheus
instance. Never assume a particular SSID, device, address range, scrape job,
or metric schema.

## Operating Principles

- Treat Prometheus as observation, not control. Do not claim to have changed
  the network, restarted equipment, or fixed an issue.
- Start with the reported symptom, affected place/device, and time window.
  Ask for the missing one only when it materially narrows the investigation.
- Use current values for a present-state question and a range query for an
  intermittent problem, trend, or comparison with an earlier period.
- Establish a comparison before calling something abnormal: compare affected
  and unaffected clients/APs, or the incident window and a recent baseline.
- Distinguish evidence from inference. State when telemetry is absent, stale,
  aggregated, or unable to prove the root cause.
- Do not reveal credentials, tokens, or raw private configuration in replies.
  It is appropriate to use live household names and identifiers when they make
  a diagnosis or report concrete and useful.

## Discovery Workflow

1. Confirm Prometheus access.
   If the Prometheus tools are unavailable, say that live network telemetry is
   unavailable rather than guessing from general networking knowledge.

2. Discover the local schema before writing specific PromQL.
   Call `prom_list_metrics` with likely prefixes (starting with `unifi` and
   `unpoller` when useful). Use `prom_series` to inspect label sets, then
   `prom_label_values` with a narrow selector to map relevant live entities.
   Metric and label names vary across Unpoller versions and configurations.

3. Select the smallest evidence set.
   For a user-reported client problem, identify the client, its AP/radio when
   available, comparable clients, and the gateway/WAN context. For a general
   health report, select representative gateway, WAN, AP, radio, client, and
   switch/port signals that actually exist in the discovered schema.

4. Query current state, then the incident window.
   Use `prom_query` for current status. Use `prom_query_range` for a bounded
   incident window and a resolution suited to the question (normally minutes,
   not seconds). Keep queries narrow enough to stay within MCP limits.

5. Synthesize a practical conclusion.
   Report what is healthy, what is degraded, the evidence and time window,
   the most likely fault domain, and the least disruptive next check. Do not
   turn a correlation into a certain cause.

## Failure-Domain Workflow

Work outward from the local network, but treat each result as evidence for a
hypothesis rather than a definitive cause. Use only layers that have live
telemetry or configured Blackbox targets:

1. **LAN and switching** — establish whether the monitoring path and relevant
   switches or wired links are reachable and healthy.
2. **Gateway** — compare gateway availability with the local infrastructure.
3. **External IP reachability** — test a configured IP probe to separate WAN
   reachability from DNS or application behaviour.
4. **DNS** — compare a configured DNS probe with IP reachability.
5. **Wi-Fi/AP** — inspect AP/radio health and affected wireless clients.
6. **One client or device** — compare it with clients sharing the same AP,
   location, or service.

For example, a healthy switch observation alongside a failed gateway probe
narrows the investigation to the gateway, its upstream link, or the probe
path; it does not prove a gateway failure. A healthy IP probe with a failed
DNS probe narrows the issue to name resolution or its path. Always check
whether the observation itself is stale or whether the monitoring/exporter
path is unavailable before drawing either conclusion.

## Event Classification and Safe Investigation

UniFi status events are symptoms, not root-cause statements. Correlate their
timestamps with lower-level telemetry before interpreting them. Where the
available metrics permit, distinguish among:

- device reboot or uptime reset;
- Ethernet link flap, port errors, or speed renegotiation;
- PoE loss or insufficient PoE capacity;
- controller-to-device communication loss;
- gateway or WAN-path loss; and
- Wi-Fi association, roaming, or a single-client disconnect.

Do not label a negotiated Fast Ethernet / 100 Mbps link as a cable fault
without comparing it with the connected device's expected capability. Likewise,
do not blame PoE without evidence of a power event, budget pressure, or a
corresponding device restart.

During an active incident, preserve evidence: build a timeline, avoid
recommending a reboot unless necessary, and prefer one smallest reversible
test that discriminates between competing hypotheses. Clearly separate the
observations, likely explanation, and confirmed root cause.

## Diagnostic Lenses

Use the lenses supported by the discovered metrics; skip a lens when there is
no relevant telemetry.

### Connectivity and WAN

Look for gateway reachability, WAN status, packet loss, latency, jitter,
throughput, and error trends. A healthy local Wi-Fi radio with a degraded WAN
path points away from an in-home coverage issue. Conversely, WAN health alone
does not prove that an individual wireless client has a good connection.

Use Blackbox probe evidence to test the path *from the exporter host* to a
configured target. It is valuable for checking an external service or a
network boundary, but it cannot prove the experience of a phone or laptop on
Wi-Fi. Compare its timing and availability with UniFi telemetry to locate the
likely boundary of an outage.

### Wi-Fi Coverage and Radio Quality

For the affected client and its associated AP/radio, inspect signal quality,
noise/interference, channel or band, retransmissions/retries, PHY/rate
indicators, roaming/disconnect events, and AP utilisation when those metrics
exist. Compare with clients on the same AP and similar clients on other APs
before attributing a problem to coverage, interference, or congestion.

### Capacity and Infrastructure Health

Inspect AP/gateway/switch availability, CPU and memory pressure, client
counts, radio airtime/utilisation, port/link state, PoE where available, and
error/discard counters. Sustained saturation or errors deserve attention;
brief spikes may be normal unless they align with the reported symptom.

### Scope and Time Correlation

Classify the issue as one client, one location/AP, one wired segment, the
whole LAN, or WAN/upstream. Correlate changes in client state and radio/AP
health with the stated time of impact. If timestamps do not overlap, describe
the observations separately instead of inferring causation.

## Blackbox Exporter Probes

The standard Blackbox Exporter exposes `probe_` metrics for HTTP/HTTPS, TCP,
DNS, ICMP, and other configured module types. The Docker image does **not**
define which targets are probed or which Prometheus labels identify them; the
scrape configuration and relabeling do. Discover the actual labels and target
series before filtering or naming a target in a report.

### Probe Workflow

1. Discover `probe_` metrics and inspect `probe_success` series and labels.
   Identify the probe target, module, and scrape job from live series rather
   than assuming `instance`, `target`, or a job name.
2. Establish availability over the requested window. `probe_success` is `1`
   for a successful probe and `0` for a failed probe. Prefer an availability
   rate and the timing of failures to a single current sample.
3. Separate a target/probe failure from monitoring failure. Check the
   Prometheus `up` series for the Blackbox scrape job when present: `up=0`
   means Prometheus could not scrape the exporter path, whereas a scraped
   `probe_success=0` means the probe itself failed.
4. For slow probes, compare `probe_duration_seconds` with
   `probe_timeout_seconds`; a ratio near `1` indicates timeout pressure.
   Use a range query to determine whether this is sustained or isolated.
5. For HTTP(S), inspect whichever of these are present: HTTP status code,
   phase durations, redirect count, TLS/SSL state, and certificate-expiry
   time. For DNS, TCP, or ICMP probes, use their discovered protocol-specific
   timing and result metrics. Do not diagnose a protocol-specific fault from
   `probe_success` alone.

### Interpret Carefully

- A failed probe is evidence from one vantage point at one time. It may be a
  target outage, DNS or routing failure, blocked ICMP/TCP/HTTP traffic, TLS
  validation failure, or an exporter/probe configuration issue.
- HTTP status and TLS certificate evidence concerns the configured endpoint;
  it does not measure general Wi-Fi quality.
- Do not expose raw probe URLs, credentials, query parameters, or private
  addresses unless the household explicitly needs them to act on the finding.
- Report certificate expiry as a date and remaining time, not only a Unix
  timestamp.

## Active Point-in-Time Tests

If `run_python_script` is available, use the bundled helper for a narrowly
scoped present-state test when Prometheus and Blackbox history cannot separate
two concrete hypotheses. The test runs from the tools container's vantage
point, not from an affected client.

Use Prometheus/Blackbox first for trend and incident evidence. Then test one
known host, port, or URL at a time. Never use this helper for host discovery,
port ranges, address ranges, credentialed URLs, or repeated polling. State the
target category and the tools-container vantage point in the report; omit raw
private addresses and URLs unless the household needs them to act.

The helper supports DNS resolution, one TCP connection, and one HTTP(S) HEAD
request. It uses only the Python standard library, returns JSON, and caps TCP
and HTTP(S) timeouts at 10 seconds. It does not provide ICMP or traceroute:
Blackbox is the preferred, low-noise source for configured ICMP/TCP/DNS/HTTP
probes.

Call it through `run_python_script` with a small wrapper, for example:

```python
import subprocess, sys

result = subprocess.run(
    [sys.executable,
     "/workspace/skills/unifi-prometheus-network/scripts/network_probe.py",
     "tcp", "--host", "known-host", "--port", "443", "--timeout", "5"],
    capture_output=True, text=True,
)
print(result.stdout or result.stderr)
```

Use `dns --host NAME`, `tcp --host HOST --port PORT`, or `http --url URL`.
Do not imply that a successful tools-container probe proves a Wi-Fi client's
experience, and do not use the helper when the Python tool is unavailable.

## Reporting Format

For a diagnosis or status report, keep the answer clear and decision-oriented:

1. **Summary** — healthy, degraded, or inconclusive; include scope.
2. **Evidence** — a few material measurements, entities, and their time window.
3. **Interpretation** — likely fault domain and confidence, with alternatives.
4. **Next step** — the smallest safe check the household can perform, or the
   next read-only measurement needed to reduce uncertainty.

Do not dump raw PromQL, complete label sets, or all time-series datapoints
unless the user asks for the technical detail.

## Prometheus Tool Use

- `prom_list_metrics` discovers metric names; use a prefix whenever possible.
- `prom_series` discovers label sets without retrieving data.
- `prom_label_values` maps live labels such as devices, sites, interfaces, or
  clients after first restricting the selector.
- `prom_query` checks a current value or compact aggregate.
- `prom_query_range` establishes trends and incident correlations. Its output
  already includes min, max, average, and latest values; prefer those summaries
  over manually interpreting every datapoint.

For Blackbox metrics, begin with `prom_list_metrics(prefix="probe_")`, then
use `prom_series` against the discovered metric to learn labels. Typical
standard metrics include `probe_success`, `probe_duration_seconds`, and
`probe_timeout_seconds`; protocol-specific metrics depend on the configured
probe module and exporter version.

Prometheus MCP limits query range, resolution, series count, and response
size. Narrow the entity selector or shorten the time window when a query is
rejected or too broad; do not repeatedly retry the same broad query.
