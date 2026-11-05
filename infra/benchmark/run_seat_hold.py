#!/usr/bin/env python3
"""Compares seat-hold with its Python baseline. Usage is in README.md."""

import argparse
import http.client
import json
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

from run import NETWORK, OHA, ROOT, docker, stop

IMAGES = {"rust": "ticketing/seat-hold", "python": "ticketing/bench-python-seat-hold"}
SERVICE_CPUS = {1: "0", 2: "0-1", 4: "0-3"}
DB_CPUS = "4-6"
LOAD_CPUS = "7-9"
DB = "bench-db"
SERVICE = "bench-seat-hold"
URL = f"http://{SERVICE}:8000/api/v1/holds"
EVENT = "01a1a5f0-0000-7000-8000-000000000001"
HOT_SEAT = "01a1a5f0-0000-7000-8000-00000000000a"
HOT_BODY = json.dumps({"event_id": EVENT, "seat_ids": [HOT_SEAT]})
SEATS = 200_000
WARMUP_SECONDS = 10
PARITY_PORTS = {"rust": 18091, "python": 18092}


def psql(sql: str) -> str:
    result = subprocess.run(
        ["docker", "exec", "-i", DB, "psql", "-U", "postgres", "-d", "seat_hold_db", "-At", "-q"],
        input=sql, capture_output=True, text=True,
    )  # fmt: skip
    if result.returncode != 0 or "ERROR" in result.stderr:
        sys.exit(result.stderr)
    return result.stdout.strip()


def start_db() -> None:
    stop(DB)
    docker("network", "create", NETWORK, check=False)
    docker(
        "run", "-d", "--rm", "--name", DB, "--network", NETWORK, "--cpuset-cpus", DB_CPUS,
        "--log-driver", "none", "-e", "POSTGRES_PASSWORD=password", "-e", "POSTGRES_DB=seat_hold_db",
        "postgres:18-alpine",
    )  # fmt: skip
    for _ in range(100):
        ready = docker(
            "exec",
            DB,
            "psql",
            "-U",
            "postgres",
            "-d",
            "seat_hold_db",
            "-Atc",
            "SELECT 1",
            check=False,
        )
        if ready == "1":
            break
        time.sleep(0.2)
    migrations = sorted(
        (ROOT.parents[1] / "services/seat-hold/migrations").glob("*.sql")
    )
    psql("".join(path.read_text() for path in migrations))


def start_service(
    kind: str, *, cpus: int, ttl: float, name: str = SERVICE, publish: int | None = None
) -> None:
    ports = ["-p", f"127.0.0.1:{publish}:8000"] if publish else []
    env = {
        "DB__HOST": DB, "DB__NAME": "seat_hold_db", "DB__USER": "postgres", "DB__PASS": "password",
        "POOL__MAX_CONNECTIONS": str(10 * cpus if kind == "rust" else 10), "HOLDS__TTL_SECONDS": str(ttl),
        "HOLDS__CLEANUP_INTERVAL_SECONDS": "3600", "WORKERS": str(cpus),
    }  # fmt: skip
    options = [item for key, value in env.items() for item in ("-e", f"{key}={value}")]
    docker(
        "run", "-d", "--rm", "--name", name, "--network", NETWORK, *ports,
        "--cpuset-cpus", SERVICE_CPUS[cpus], "--memory", "512m", "--log-driver", "none",
        *options, IMAGES[kind],
    )  # fmt: skip


def oha(options: list[str], bodies: Path) -> dict:
    report = docker(
        "run", "--rm", "--network", NETWORK, "--cpuset-cpus", LOAD_CPUS, "-v", f"{bodies.parent}:/bodies:ro",
        OHA, "--no-tui", "--output-format", "json", "-m", "POST", "-T", "application/json",
        "-H", "x-user-id: bench", *options, URL,
    )  # fmt: skip
    return json.loads(report)


def wait_ready(bodies: Path) -> None:
    for _ in range(100):
        report = oha(["-n", "1", "-d", HOT_BODY], bodies)
        if not report["errorDistribution"] and report["statusCodeDistribution"]:
            return
        time.sleep(0.1)
    sys.exit("seat-hold did not become ready")


def sample_cpu(result: dict, delay: float) -> None:
    time.sleep(delay)
    stats = docker(
        "stats", "--no-stream", "--format", "{{.Name}} {{.CPUPerc}}", SERVICE, DB
    )
    for line in stats.splitlines():
        name, value = line.split()
        result[f"cpu_{'db' if name == DB else 'service'}"] = float(value.rstrip("%"))


def load(
    args: argparse.Namespace, bodies: Path, seconds: int, sample: bool = False
) -> dict:
    source = (
        ["-Z", f"/bodies/{bodies.name}"]
        if args.scenario == "free"
        else ["-d", HOT_BODY]
    )
    options = ["-z", f"{seconds}s", "-c", str(args.connections), "-w", *source]
    if args.rate:
        options += ["-q", str(args.rate), "--latency-correction"]
    usage: dict = {}
    sampler = (
        threading.Thread(target=sample_cpu, args=(usage, seconds / 2))
        if sample
        else None
    )
    if sampler:
        sampler.start()
    report = oha(options, bodies)
    if sampler:
        sampler.join()
    codes = report["statusCodeDistribution"]
    return {
        "rps": report["summary"]["requestsPerSec"],
        "p50_ms": report["latencyPercentiles"]["p50"] * 1000,
        "p99_ms": report["latencyPercentiles"]["p99"] * 1000,
        "created": codes.get("201", 0),
        "conflict": codes.get("409", 0),
        "failed": sum(n for code, n in codes.items() if code not in ("201", "409"))
        + sum(report["errorDistribution"].values()),
        **usage,
    }


def one_run(kind: str, args: argparse.Namespace, bodies: Path) -> dict:
    psql("TRUNCATE holds;")
    ttl = 0.001 if args.scenario == "free" else 300.0
    if args.scenario == "hot":
        psql(
            f"INSERT INTO holds VALUES ('{EVENT}', '{HOT_SEAT}', gen_random_uuid(), 'first', now() + interval '1 hour');"
        )
    start_service(kind, cpus=args.cpus, ttl=ttl)
    try:
        wait_ready(bodies)
        load(args, bodies, WARMUP_SECONDS)
        result = load(args, bodies, args.seconds, sample=True)
        peak = docker("exec", SERVICE, "cat", "/sys/fs/cgroup/memory.peak")
        result["peak_mb"] = int(peak) / 2**20
        return result
    finally:
        stop(SERVICE)


def report(title: str, runs: dict[str, list[dict]]) -> None:
    print(f"\n## {title}")
    for kind, results in runs.items():
        cells = []
        for key in ("rps", "p50_ms", "p99_ms", "peak_mb", "cpu_service", "cpu_db"):
            values = [r[key] for r in results if key in r]
            if values:
                cells.append(
                    f"{key}={statistics.median(values):.1f} [{min(values):.1f}-{max(values):.1f}]"
                )
        totals = {
            key: sum(r[key] for r in results)
            for key in ("created", "conflict", "failed")
        }
        print(f"{kind:7} {' '.join(cells)} {totals}", flush=True)


def write_bodies(directory: str) -> Path:
    path = Path(directory) / "bodies.txt"
    lines = (
        json.dumps({"event_id": EVENT, "seat_ids": [str(uuid.uuid4())]})
        for _ in range(SEATS)
    )
    path.write_text("\n".join(lines) + "\n")
    return path


def load_command(args: argparse.Namespace) -> None:
    start_db()
    runs: dict[str, list[dict]] = {"rust": [], "python": []}
    with tempfile.TemporaryDirectory() as directory:
        bodies = write_bodies(directory)
        try:
            for index in range(args.runs):
                order = ["rust", "python"] if index % 2 == 0 else ["python", "rust"]
                for kind in order:
                    result = one_run(kind, args, bodies)
                    runs[kind].append(result)
                    print(f"run {index + 1} {kind}: {result}", flush=True)
        finally:
            stop(SERVICE, DB)
    report(
        f"scenario={args.scenario} cpus={args.cpus} connections={args.connections} rate={args.rate}",
        runs,
    )


def request(
    port: int, method: str, path: str, user: str | None, body: object = None
) -> tuple[int, dict]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"content-type": "application/json"}
    if user is not None:
        headers["x-user-id"] = user
    connection.request(
        method, path, json.dumps(body) if body is not None else None, headers
    )
    response = connection.getresponse()
    text = response.read().decode()
    connection.close()
    return response.status, json.loads(text) if text else {}


def wait_http(port: int) -> None:
    for _ in range(2000):
        try:
            if request(port, "GET", "/ready", None)[0] == 200:
                return
        except OSError, http.client.HTTPException:
            time.sleep(0.005)
    sys.exit(f"port {port} did not become ready")


def parity(_: argparse.Namespace) -> None:
    start_db()
    outcomes: dict[str, list[tuple]] = {}
    for kind, port in PARITY_PORTS.items():
        psql("TRUNCATE holds;")
        start_service(kind, cpus=1, ttl=300.0, name=f"{SERVICE}-{kind}", publish=port)
        wait_http(port)
        event, seat_a, seat_b, seat_c = (str(uuid.uuid4()) for _ in range(4))
        free = {"event_id": event, "seat_ids": [seat_a, seat_b]}
        seen = []

        def check(name: str, *call: object) -> dict:
            status, body = request(port, *call)
            details = len(body.get("details", []))
            seen.append(
                (
                    name,
                    status,
                    body.get("code"),
                    details,
                    sorted(k for k in body if k != "message"),
                )
            )
            return body

        hold = check("free seats", "POST", "/api/v1/holds", "alice", free)
        check(
            "taken seat",
            "POST",
            "/api/v1/holds",
            "bob",
            {"event_id": event, "seat_ids": [seat_b, seat_c]},
        )
        check(
            "seat left free",
            "POST",
            "/api/v1/holds",
            "bob",
            {"event_id": event, "seat_ids": [seat_c]},
        )
        check("no user", "POST", "/api/v1/holds", None, free)
        check("empty user", "POST", "/api/v1/holds", "", free)
        check(
            "no seats",
            "POST",
            "/api/v1/holds",
            "alice",
            {"event_id": event, "seat_ids": []},
        )
        check(
            "duplicates",
            "POST",
            "/api/v1/holds",
            "alice",
            {"event_id": event, "seat_ids": [seat_a, seat_a]},
        )
        many = [str(uuid.uuid4()) for _ in range(11)]
        check(
            "over the limit",
            "POST",
            "/api/v1/holds",
            "alice",
            {"event_id": event, "seat_ids": many},
        )
        check(
            "malformed id",
            "POST",
            "/api/v1/holds",
            "alice",
            {"event_id": "nope", "seat_ids": []},
        )
        release = f"/api/v1/holds/{hold['hold_id']}"
        alice_rows = (
            "SELECT count(*) FROM holds "
            f"WHERE event_id = '{event}' AND owner_id = 'alice'"
        )
        check("release by another user", "DELETE", release, "bob")
        seen.append(("rows after that", psql(alice_rows)))
        check("release", "DELETE", release, "alice")
        check("release again", "DELETE", release, "alice")
        check("release bad id", "DELETE", "/api/v1/holds/nope", "alice")
        check("release no user", "DELETE", release, None)
        seen.append(("rows at the end", psql(alice_rows)))
        outcomes[kind] = seen
        stop(f"{SERVICE}-{kind}")
    stop(DB)
    same = 0
    for rust, python in zip(outcomes["rust"], outcomes["python"], strict=True):
        matched = rust == python
        same += matched
        print(
            f"{'same' if matched else 'DIFF'} {rust}"
            + ("" if matched else f"\n     {python}")
        )
    total = len(outcomes["rust"])
    print(f"\n{same}/{total} cases match")
    if same != total:
        sys.exit(1)


def footprint(args: argparse.Namespace) -> None:
    start_db()
    for kind, image in IMAGES.items():
        size = int(docker("image", "inspect", image, "--format", "{{.Size}}")) / 2**20
        starts, idle = [], []
        for _ in range(args.runs):
            started = time.perf_counter()
            start_service(kind, cpus=1, ttl=300.0, publish=18093)
            wait_http(18093)
            starts.append(time.perf_counter() - started)
            time.sleep(2)
            idle.append(
                int(docker("exec", SERVICE, "cat", "/sys/fs/cgroup/memory.current"))
                / 2**20
            )
            stop(SERVICE)
        print(
            f"{kind:7} image={size:.0f}MB idle={statistics.median(idle):.1f}MB start={statistics.median(starts):.2f}s",
            flush=True,
        )
    stop(DB)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["parity", "load", "footprint"])
    parser.add_argument("--scenario", choices=["free", "hot"], default="free")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--seconds", type=int, default=30)
    parser.add_argument("--cpus", type=int, choices=SERVICE_CPUS, default=1)
    parser.add_argument("--connections", type=int, default=64)
    parser.add_argument("--rate", type=int)
    args = parser.parse_args()

    docker("build", "-q", "-t", IMAGES["python"], str(ROOT / "python-seat-hold"))
    docker(
        "build", "-q", "-t", IMAGES["rust"], str(ROOT.parents[1] / "services/seat-hold")
    )
    {"parity": parity, "load": load_command, "footprint": footprint}[args.command](args)


if __name__ == "__main__":
    main()
