#!/usr/bin/env python3
"""Compares the Rust gateway with its Python baseline. Usage is in README.md."""

import argparse
import base64
import hashlib
import hmac
import http.client
import json
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

OHA = "ghcr.io/hatoo/oha:1.16.0"
ROOT = Path(__file__).parent
NETWORK = "bench"
STACK_NETWORK = "ticketing_default"
STACK_CONTAINERS = ("ticketing-catalog-1", "ticketing-catalog-db-1")
SECRET = "benchmark-secret-with-at-least-32-bytes"
IMAGES = {"rust": "ticketing/gateway", "python": "ticketing/bench-python-proxy"}
PROXY_CPUS = {1: "0", 2: "0-1"}
UPSTREAM_CPUS = "2-3"
LOAD_CPUS = "4-7"
STUB_URL = "http://bench-stub:8000"
EVENTS_PATH = "/api/v1/events"
WARMUP_SECONDS = 10


def docker(*args: str, check: bool = True) -> str:
    result = subprocess.run(["docker", *args], capture_output=True, text=True)
    if check and result.returncode != 0:
        sys.exit(f"docker {' '.join(args)}\n{result.stderr}")
    return result.stdout.strip()


def encoded(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def token(*, exp_in: int = 3600, secret: str = SECRET) -> str:
    header = encoded(b'{"alg":"HS256","typ":"JWT"}')
    claims = {"sub": "bench-user", "exp": int(time.time()) + exp_in}
    payload = encoded(json.dumps(claims).encode())
    signature = hmac.new(
        secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256
    )
    return f"{header}.{payload}.{encoded(signature.digest())}"


def build() -> None:
    docker("build", "-q", "-t", "ticketing/bench-stub", str(ROOT / "stub"))
    docker("build", "-q", "-t", IMAGES["python"], str(ROOT / "python-proxy"))
    docker(
        "build", "-q", "-t", IMAGES["rust"], str(ROOT.parents[1] / "services/gateway")
    )
    docker("network", "create", NETWORK, check=False)


def stop(*names: str) -> None:
    docker("rm", "-f", *names, check=False)


def start_stub(*, delay_ms: int = 0) -> None:
    docker(
        "run", "-d", "--rm", "--name", "bench-stub", "--network", NETWORK,
        "--cpuset-cpus", UPSTREAM_CPUS, "--log-driver", "none",
        "-e", f"DELAY_MS={delay_ms}", "ticketing/bench-stub",
    )  # fmt: skip


def start_proxy(
    kind: str, *, cpus: int, upstream: str, network: str, publish: int | None = None
) -> str:
    name = f"bench-{kind}"
    ports = ["-p", f"127.0.0.1:{publish}:8000"] if publish else []
    docker(
        "run", "-d", "--rm", "--name", name, "--network", network, *ports,
        "--cpuset-cpus", PROXY_CPUS[cpus], "--memory", "512m", "--log-driver", "none",
        "-e", f"AUTH__JWT_SECRET={SECRET}", "-e", f"UPSTREAMS__CATALOG_URL={upstream}", "-e", f"UPSTREAMS__SEAT_HOLD_URL={upstream}",
        "-e", f"WORKERS={cpus}", IMAGES[kind],
    )  # fmt: skip
    return name


def oha(url: str, network: str, *options: str) -> dict:
    report = docker(
        "run", "--rm", "--network", network, "--cpuset-cpus", LOAD_CPUS, OHA,
        "--no-tui", "--output-format", "json",
        "-H", f"Authorization: Bearer {token()}", *options, url,
    )  # fmt: skip
    return json.loads(report)


def wait_ready(url: str, network: str) -> None:
    for _ in range(100):
        report = oha(url, network, "-n", "1")
        if report["statusCodeDistribution"].get("200"):
            return
    sys.exit(f"{url} did not become ready")


def load(
    url: str, network: str, *, connections: int, seconds: int, rate: int | None
) -> dict:
    options = ["-z", f"{seconds}s", "-c", str(connections), "-w"]
    if rate:
        options += ["-q", str(rate), "--latency-correction"]
    report = oha(url, network, *options)
    codes = report["statusCodeDistribution"]
    return {
        "rps": report["summary"]["requestsPerSec"],
        "p50_ms": report["latencyPercentiles"]["p50"] * 1000,
        "p99_ms": report["latencyPercentiles"]["p99"] * 1000,
        "ok": codes.get("200", 0),
        "failed": sum(n for code, n in codes.items() if code != "200")
        + sum(report["errorDistribution"].values()),
    }


def memory_mb(name: str, metric: str) -> float:
    return int(docker("exec", name, "cat", f"/sys/fs/cgroup/memory.{metric}")) / 2**20


def one_run(
    target: str, *, cpus: int, delay_ms: int, connections: int, seconds: int,
    rate: int | None, stack: bool,
) -> dict:  # fmt: skip
    network = STACK_NETWORK if stack else NETWORK
    upstream = "http://catalog:8000" if stack else STUB_URL
    if not stack:
        start_stub(delay_ms=delay_ms)
    name = None
    url = f"{upstream}{EVENTS_PATH}"
    if target != "direct":
        name = start_proxy(target, cpus=cpus, upstream=upstream, network=network)
        url = f"http://{name}:8000{EVENTS_PATH}"
    try:
        wait_ready(url, network)
        load(url, network, connections=connections, seconds=WARMUP_SECONDS, rate=rate)
        result = load(url, network, connections=connections, seconds=seconds, rate=rate)
        if name:
            result["peak_mb"] = memory_mb(name, "peak")
        return result
    finally:
        stop("bench-stub", "bench-rust", "bench-python")


def report(title: str, runs: dict[str, list[dict]]) -> None:
    print(f"\n## {title}")
    for target, results in runs.items():
        cells = []
        for key in ("rps", "p50_ms", "p99_ms", "peak_mb"):
            values = [r[key] for r in results if key in r]
            if values:
                cells.append(
                    f"{key}={statistics.median(values):.1f}"
                    f" [{min(values):.1f}-{max(values):.1f}]"
                )
        failed = sum(r["failed"] for r in results)
        ok = sum(r["ok"] for r in results)
        print(f"{target:7} {' '.join(cells)} ok={ok} failed={failed}", flush=True)


def scenario(args: argparse.Namespace) -> None:
    targets = ["rust", "python"] + (["direct"] if args.stack else [])
    runs: dict[str, list[dict]] = {target: [] for target in targets}
    if args.stack:
        docker("update", "--cpuset-cpus", UPSTREAM_CPUS, *STACK_CONTAINERS)
    try:
        for index in range(args.runs):
            order = targets if index % 2 == 0 else list(reversed(targets))
            for target in order:
                result = one_run(
                    target, cpus=args.cpus, delay_ms=args.delay_ms,
                    connections=args.connections, seconds=args.seconds,
                    rate=args.rate, stack=args.stack,
                )  # fmt: skip
                runs[target].append(result)
                print(f"run {index + 1} {target}: {result}", flush=True)
    finally:
        if args.stack:
            docker("update", "--cpuset-cpus", "0-9", *STACK_CONTAINERS)
    report(
        f"cpus={args.cpus} connections={args.connections} delay_ms={args.delay_ms}"
        f" rate={args.rate} stack={args.stack}",
        runs,
    )


def request(
    port: int,
    method: str,
    path: str,
    headers: dict[str, str],
    body: bytes | None = None,
) -> tuple[int, dict[str, str], str]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    text = response.read().decode(errors="replace")
    connection.close()
    return response.status, {k.lower(): v for k, v in response.getheaders()}, text


def wait_port(port: int) -> float:
    started = time.perf_counter()
    while time.perf_counter() - started < 30:
        try:
            if request(port, "GET", "/ready", {})[0] == 200:
                return time.perf_counter() - started
        except OSError:
            time.sleep(0.005)
    sys.exit(f"port {port} did not become ready")


def compare(
    name: str, ports: dict[str, int], *request_args, check_body: bool = True
) -> bool:
    answers = {kind: request(port, *request_args) for kind, port in ports.items()}
    rust, python = answers["rust"], answers["python"]
    same = rust[0] == python[0] and (not check_body or rust[2] == python[2])
    if rust[0] == 401:
        same = same and rust[1].get("www-authenticate") == python[1].get(
            "www-authenticate"
        )
    print(f"{'same' if same else 'DIFF'} {name}: rust={rust[0]} python={python[0]}")
    if not same:
        print(f"  rust:   {rust[2][:160]}\n  python: {python[2][:160]}")
    return same


def upstream_view(ports: dict[str, int]) -> bool:
    headers = {
        "Authorization": f"Bearer {token()}",
        "X-User-Id": "admin",
        "Traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        "Tracestate": "vendor=client",
        "Proxy-Authorization": "Basic secret",
        "X-Custom": "kept",
    }
    views = {}
    for kind, port in ports.items():
        text = request(port, "GET", f"{EVENTS_PATH}/echo", headers)[2]
        lines = dict(line.split(": ", 1) for line in text.splitlines())
        trace = lines.get("traceparent", "")
        views[kind] = {
            "names": sorted(lines),
            "user": lines.get("x-user-id"),
            "custom": lines.get("x-custom"),
            "new_trace": bool(re.fullmatch(r"00-[0-9a-f]{32}-[0-9a-f]{16}-01", trace))
            and "4bf92f3577b34da6a3ce929d0e0e4736" not in trace,
        }
    same = views["rust"] == views["python"]
    print(f"{'same' if same else 'DIFF'} upstream view: {views['rust']}")
    if not same:
        print(f"  python: {views['python']}")
    return same


def parity(_: argparse.Namespace) -> None:
    ports = {"rust": 18081, "python": 18082}
    valid = {"Authorization": f"Bearer {token()}"}
    forged = {"Authorization": f"Bearer {token(secret='x' * 40)}"}
    expired = {"Authorization": f"Bearer {token(exp_in=-1)}"}
    basic = {"Authorization": f"Basic {token()}"}
    results = []
    start_stub()
    try:
        for kind, port in ports.items():
            start_proxy(kind, cpus=1, upstream=STUB_URL, network=NETWORK, publish=port)
            wait_port(port)
        results += [
            compare("valid GET", ports, "GET", EVENTS_PATH, valid),
            compare(
                "valid GET with query", ports, "GET", f"{EVENTS_PATH}?limit=1", valid
            ),
            compare("valid POST", ports, "POST", EVENTS_PATH, valid, b"payload"),
            compare("no token", ports, "GET", EVENTS_PATH, {}),
            compare(
                "garbage token",
                ports,
                "GET",
                EVENTS_PATH,
                {"Authorization": "Bearer a.b.c"},
            ),
            compare("forged token", ports, "GET", EVENTS_PATH, forged),
            compare("expired token", ports, "GET", EVENTS_PATH, expired),
            compare("another scheme", ports, "GET", EVENTS_PATH, basic),
            compare(
                "dots in path", ports, "GET", f"{EVENTS_PATH}/../../../health", valid
            ),
            compare(
                "encoded dots",
                ports,
                "GET",
                f"{EVENTS_PATH}/%2e%2e/%2e%2e/health",
                valid,
            ),
            compare(
                "body at the limit", ports, "POST", EVENTS_PATH, valid, b"x" * 2**20
            ),
            compare(
                "body over the limit",
                ports,
                "POST",
                EVENTS_PATH,
                valid,
                b"x" * (2**20 + 1),
            ),
            compare(
                "unknown route", ports, "GET", "/api/v1/nope", valid, check_body=False
            ),
            upstream_view(ports),
        ]
        stop("bench-stub")
        results.append(compare("upstream down", ports, "GET", EVENTS_PATH, valid))
        start_stub(delay_ms=11_000)
        time.sleep(0.5)
        results.append(compare("upstream too slow", ports, "GET", EVENTS_PATH, valid))
    finally:
        stop("bench-stub", "bench-rust", "bench-python")
    print(f"\n{sum(results)}/{len(results)} cases match")
    if not all(results):
        sys.exit(1)


def footprint(args: argparse.Namespace) -> None:
    for kind, image in IMAGES.items():
        size = int(docker("image", "inspect", image, "--format", "{{.Size}}")) / 2**20
        starts, idle = [], []
        for _ in range(args.runs):
            started = time.perf_counter()
            name = start_proxy(
                kind, cpus=1, upstream=STUB_URL, network=NETWORK, publish=18083
            )
            wait_port(18083)
            starts.append(time.perf_counter() - started)
            time.sleep(2)
            idle.append(memory_mb(name, "current"))
            stop(name)
        print(
            f"{kind:7} image={size:.0f}MB idle={statistics.median(idle):.1f}MB"
            f" start={statistics.median(starts):.2f}s"
            f" [{min(starts):.2f}-{max(starts):.2f}]",
            flush=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["parity", "load", "footprint"])
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--seconds", type=int, default=30)
    parser.add_argument("--cpus", type=int, choices=PROXY_CPUS, default=1)
    parser.add_argument("--connections", type=int, default=64)
    parser.add_argument("--delay-ms", type=int, default=0)
    parser.add_argument("--rate", type=int)
    parser.add_argument("--stack", action="store_true", help="use the real catalog")
    args = parser.parse_args()

    build()
    commands = {"parity": parity, "load": scenario, "footprint": footprint}
    commands[args.command](args)


if __name__ == "__main__":
    main()
