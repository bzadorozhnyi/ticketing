import base64
import json
import subprocess
import tempfile
from pathlib import Path


class Stack:
    """The running system the agent works on: the kind cluster or compose. Issues
    tokens, runs SQL for a scenario's data and for the graders, and says where the MCP
    server is."""

    def __init__(self, *, kind: str, kubeconfig: str | None = None):
        if kind not in ("kind", "compose"):
            raise ValueError(f"unknown stack {kind!r}")
        self.kind = kind
        self.kubeconfig = kubeconfig
        self.mcp_url = (
            "https://localhost:8000/mcp"
            if kind == "kind"
            else "http://localhost:8000/mcp"
        )
        self.ca_file = self._edge_certificate() if kind == "kind" else None

    def _kubectl(self, *args: str) -> list[str]:
        config = ["--kubeconfig", self.kubeconfig] if self.kubeconfig else []
        return ["kubectl", *config, "-n", "ticketing", *args]

    def _run(self, command: list[str], stdin: str | None = None) -> str:
        done = subprocess.run(
            command, input=stdin, capture_output=True, text=True, check=True
        )
        return done.stdout.strip()

    def _edge_certificate(self) -> str:
        encoded = self._run(
            self._kubectl(
                "get", "secret", "edge-tls", "-o", "jsonpath={.data.tls\\.crt}"
            )
        )
        path = Path(tempfile.mkdtemp(prefix="evals-")) / "edge.crt"
        path.write_bytes(base64.b64decode(encoded))
        return str(path)

    def token(self, user_id: str) -> str:
        if self.kind == "kind":
            return self._run(
                self._kubectl("exec", "deploy/gateway", "--", "issue-token", user_id)
            )
        return self._run(
            ["docker", "compose", "exec", "-T", "gateway", "issue-token", user_id]
        )

    def sql(self, database: str, query: str) -> list[list[str]]:
        """Rows of the last statement, as text. `database` is catalog, seat-hold or
        booking."""
        command = self._psql(database) + ["-v", "ON_ERROR_STOP=1", "-tA", "-F", "\t"]
        output = self._run(command, stdin=query)
        return [line.split("\t") for line in output.splitlines() if line]

    def _psql(self, database: str) -> list[str]:
        name = {
            "catalog": "catalog_db",
            "seat-hold": "seat_hold_db",
            "booking": "booking_db",
        }[database]
        if self.kind == "compose":
            service = f"{database}-db"
            return [
                "docker",
                "compose",
                "exec",
                "-T",
                service,
                "psql",
                "-U",
                "postgres",
                "-d",
                name,
            ]
        if database == "booking":
            pod = self._run(
                self._kubectl(
                    "get", "pods", "-l", "cnpg.io/instanceRole=primary", "-o", "name"
                )
            )
            return self._kubectl(
                "exec",
                "-i",
                pod.splitlines()[0],
                "-c",
                "postgres",
                "--",
                "psql",
                "-d",
                name,
            )
        return self._kubectl(
            "exec", "-i", f"{database}-db-0", "--", "psql", "-U", "postgres", "-d", name
        )

    def mcp_config(self, token: str) -> str:
        server = {
            "type": "http",
            "url": self.mcp_url,
            "headers": {"Authorization": f"Bearer {token}"},
        }
        return json.dumps({"mcpServers": {"ticketing": server}})
