import json
import os
import shutil
import subprocess
import tempfile
import time
from glob import glob
from pathlib import Path
from typing import Any

from evals.runner import SYSTEM_PROMPT
from evals.stack import Stack
from evals.trajectory import TOOL_PREFIX, ToolCall, Trajectory

TOOLS = (
    "search_events",
    "get_event_sections",
    "get_section_seats",
    "hold_seats",
    "release_hold",
    "create_order",
    "get_order",
)


def find_claude() -> str:
    """`claude` on the PATH, else the binary the VS Code extension ships."""
    found = os.environ.get("CLAUDE_BIN") or shutil.which("claude")
    if found:
        return found
    bundled = sorted(
        glob(
            str(
                Path.home()
                / ".vscode/extensions"
                / "anthropic.claude-code-*/resources/native-binary/claude"
            )
        )
    )
    if not bundled:
        raise RuntimeError("Claude Code not found: set CLAUDE_BIN")
    return bundled[-1]


class ClaudeCodeRunner:
    """The agent is Claude Code in print mode, signed in with the developer's own
    subscription. Only our MCP server and its tools: no built-in tools, no settings,
    skills or project files of this machine (it runs in an empty directory)."""

    def __init__(self, *, model: str, timeout_seconds: float = 300):
        self.claude = find_claude()
        self.model = model
        self.timeout_seconds = timeout_seconds

    def run(self, *, prompt: str, user_id: str, stack: Stack) -> Trajectory:
        workdir = Path(tempfile.mkdtemp(prefix="evals-agent-"))
        config = workdir / "mcp.json"
        config.write_text(stack.mcp_config(stack.token(user_id)))
        env = dict(os.environ)
        if stack.ca_file:
            env["NODE_EXTRA_CA_CERTS"] = stack.ca_file
        command = [
            self.claude,
            "-p",
            prompt,
            "--model",
            self.model,
            "--system-prompt",
            SYSTEM_PROMPT,
            "--mcp-config",
            str(config),
            "--strict-mcp-config",
            "--tools",
            "",
            "--allowedTools",
            ",".join(TOOL_PREFIX + tool for tool in TOOLS),
            "--setting-sources",
            "local",
            "--disable-slash-commands",
            "--no-session-persistence",
            "--output-format",
            "stream-json",
            "--verbose",
        ]
        started = time.monotonic()
        try:
            done = subprocess.run(
                command,
                cwd=workdir,
                env=env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return Trajectory(
                calls=[],
                answer="",
                turns=0,
                cost_usd=0,
                seconds=self.timeout_seconds,
                failed="timeout",
            )
        trajectory = parse_stream(done.stdout, time.monotonic() - started)
        if done.returncode != 0 and trajectory.failed is None:
            trajectory.failed = f"exit {done.returncode}: {done.stderr[-300:]}"
        return trajectory


def parse_stream(stdout: str, seconds: float) -> Trajectory:
    """Calls of our tools from the stream's tool_use blocks, matched to their
    results."""
    calls: dict[str, ToolCall] = {}
    answer, turns, cost, failed = "", 0, 0.0, None
    for line in stdout.splitlines():
        event: dict[str, Any] = json.loads(line)
        kind = event.get("type")
        if kind == "assistant":
            for block in event["message"]["content"]:
                if block.get("type") == "tool_use" and block["name"].startswith(
                    TOOL_PREFIX
                ):
                    calls[block["id"]] = ToolCall(
                        name=block["name"].removeprefix(TOOL_PREFIX),
                        input=block["input"],
                    )
        elif kind == "user":
            for block in event["message"].get("content", []):
                if isinstance(block, dict) and block.get("tool_use_id") in calls:
                    call = calls[block["tool_use_id"]]
                    call.result = _text(block.get("content"))
                    call.is_error = bool(block.get("is_error"))
        elif kind == "result":
            answer = str(event.get("result") or "")
            turns = int(event.get("num_turns") or 0)
            cost = float(event.get("total_cost_usd") or 0)
            if event.get("is_error"):
                failed = event.get("subtype") or "error"
    return Trajectory(
        calls=list(calls.values()),
        answer=answer,
        turns=turns,
        cost_usd=cost,
        seconds=seconds,
        failed=failed,
    )


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            str(part.get("text", "")) for part in content if isinstance(part, dict)
        )
    return ""
