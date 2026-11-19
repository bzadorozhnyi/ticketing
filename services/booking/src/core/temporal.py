"""The only module that builds Temporal's client and worker (ADR 0018). Workflows and
activities use the SDK's decorators: they are Temporal code by nature."""

from collections.abc import Callable, Sequence
from typing import Any

from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode
from temporalio.worker import Worker

from core.settings import TemporalSettings


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
        return Worker(
            await self.client(),
            task_queue=self.settings.TASK_QUEUE,
            workflows=workflows,
            activities=activities,
        )
