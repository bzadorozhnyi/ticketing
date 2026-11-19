"""The only module that builds Temporal's client and worker (ADR 0018). Workflows and
activities use the SDK's decorators: they are Temporal code by nature."""

import asyncio
from collections.abc import Callable, Sequence
from datetime import timedelta
from typing import Any

from temporalio import activity
from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode
from temporalio.worker import (
    ActivityInboundInterceptor,
    ExecuteActivityInput,
    Interceptor,
    Worker,
)

from core.settings import TemporalSettings

STICKY_QUEUE_TIMEOUT = timedelta(seconds=3)
# A workflow task takes milliseconds here; when its worker dies mid-task the server
# waits this long before giving it to another, 10 s by default.
WORKFLOW_TASK_TIMEOUT = timedelta(seconds=2)
HEARTBEAT_INTERVAL = timedelta(seconds=0.5)


class Heartbeats(Interceptor):
    """Every activity heartbeats while it runs, also while it awaits a call. The server
    sees a dead worker only by a missed heartbeat or the end of `start_to_close`: with
    a short `heartbeat_timeout` it notices in seconds, while a live but slow attempt
    keeps beating and keeps its long `start_to_close`."""

    def intercept_activity(
        self, next: ActivityInboundInterceptor
    ) -> ActivityInboundInterceptor:
        return _HeartbeatingActivity(next)


class _HeartbeatingActivity(ActivityInboundInterceptor):
    async def execute_activity(self, input: ExecuteActivityInput) -> Any:
        beats = asyncio.create_task(_beat())
        try:
            return await self.next.execute_activity(input)
        finally:
            beats.cancel()


async def _beat() -> None:
    while True:
        activity.heartbeat()
        await asyncio.sleep(HEARTBEAT_INTERVAL.total_seconds())


class TemporalClient:
    """Connects on first use, so a process that never meets a Temporal order never
    needs the server."""

    def __init__(self, *, settings: TemporalSettings):
        self.settings = settings
        self._client: Client | None = None

    async def client(self) -> Client:
        if self._client is None:
            self._client = await Client.connect(
                self.settings.ADDRESS,
                namespace=self.settings.NAMESPACE,
                data_converter=pydantic_data_converter,
            )
        return self._client

    async def start(
        self, *, workflow: Callable[..., Any], arg: Any, workflow_id: str
    ) -> bool:
        """False when a workflow with this id exists or existed: one run per id, ever,
        so a repeat can never run a finished saga again."""
        client = await self.client()
        try:
            await client.start_workflow(
                workflow,
                arg,
                id=workflow_id,
                task_queue=self.settings.TASK_QUEUE,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                task_timeout=WORKFLOW_TASK_TIMEOUT,
            )
        except WorkflowAlreadyStartedError:
            return False
        return True

    async def signal(
        self, *, workflow_id: str, signal: Callable[..., Any], arg: Any
    ) -> bool:
        """False when the workflow has finished or does not exist."""
        client = await self.client()
        try:
            await client.get_workflow_handle(workflow_id).signal(signal, arg)
        except RPCError as exc:
            if exc.status == RPCStatusCode.NOT_FOUND:
                return False
            raise
        return True

    async def worker(
        self, *, workflows: Sequence[type], activities: Sequence[Callable[..., Any]]
    ) -> Worker:
        """A workflow's tasks go to its worker's own queue while it lives. When the
        worker dies, the server moves them to the shared queue after
        `sticky_queue_schedule_to_start_timeout`, 10 s by default."""
        return Worker(
            await self.client(),
            task_queue=self.settings.TASK_QUEUE,
            workflows=workflows,
            activities=activities,
            sticky_queue_schedule_to_start_timeout=STICKY_QUEUE_TIMEOUT,
            interceptors=[Heartbeats()],
        )
