from pydantic import BaseModel

TOOL_PREFIX = "mcp__ticketing__"


class ToolCall(BaseModel):
    name: str
    input: dict
    result: str = ""
    is_error: bool = False


class Trajectory(BaseModel):
    """What an agent did in one run: every call to our tools, in order, and its
    answer."""

    calls: list[ToolCall]
    answer: str
    turns: int
    cost_usd: float
    seconds: float
    failed: str | None = None

    def called(self, name: str) -> list[ToolCall]:
        return [call for call in self.calls if call.name == name]
