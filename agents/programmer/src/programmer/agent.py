"""
ProgrammerAgent — second concrete implementation of AgentSpec.

Generates Dockerfiles, compose blocks, Helm values. Writing files to
disk is the only destructive verb and is gated by the runtime.
"""
from __future__ import annotations

import time
from typing import Any, Sequence

from pydantic import BaseModel, ConfigDict, Field

from agentlib import (
    AgentContext,
    AgentResult,
    AgentSpec,
    CostBreakdown,
    StructuralAgent,
    TaskMessage,
    claude45,
    gate_tools,
)

from .tools import ALL_TOOLS, DESTRUCTIVE_TOOLS


SYSTEM_PROMPT = """You are the Olympus Programmer agent. You generate
deployment artifacts: Dockerfiles, docker-compose service blocks, Helm
values, and related configuration.

You can:
  - Generate artifacts as strings via the templating tools.
  - Write a generated artifact to disk via write_file (destructive — the
    runtime will request human approval before each call).

You CANNOT:
  - Run containers, deploy to clusters, or touch infrastructure — those
    are other agents' jobs (Sysadmin, Terraform, Ansible).
  - Execute shell commands or fetch network resources.

Workflow:
  1. Generate the artifact in memory first; show the user what you produced.
  2. Only call write_file once the content is finalized.
  3. Produce a structured summary with the files you generated and where.
"""


class ProgrammerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(description="One-paragraph summary of what was generated.")
    artifacts: dict[str, str] = Field(
        default_factory=dict,
        description="Mapping of artifact name → content (e.g. {'Dockerfile': '...'}).",
    )
    files_written: list[str] = Field(
        default_factory=list,
        description="Absolute paths of any files written to disk.",
    )


class ProgrammerAgent(AgentSpec):
    name = "programmer"
    domain = "Code packaging and deployment artifacts: Dockerfiles, docker-compose, Helm charts, scripts"
    tools: Sequence[Any] = ALL_TOOLS
    destructive_verbs = {t.name for t in DESTRUCTIVE_TOOLS}
    model = claude45

    def handle(self, task: TaskMessage, ctx: AgentContext) -> AgentResult:
        gated = gate_tools(self, ctx, task.task_id)
        agent = StructuralAgent(
            task_id=task.task_id,
            system_prompt=SYSTEM_PROMPT,
            response_class=ProgrammerResponse,
            model=self.model,
            tools=gated,
            agent_type=self.name,
        )

        started = time.monotonic()
        try:
            response: ProgrammerResponse = agent.invoke(task.natural_language)
            return AgentResult(
                task_id=task.task_id,
                status="success",
                summary=response.summary,
                artifacts={
                    "generated": response.artifacts,
                    "files_written": response.files_written,
                },
                cost=CostBreakdown(wall_seconds=time.monotonic() - started),
            )
        except Exception as exc:
            return AgentResult(
                task_id=task.task_id,
                status="failed",
                summary=f"Programmer agent raised {type(exc).__name__}: {exc}",
                cost=CostBreakdown(wall_seconds=time.monotonic() - started),
            )
        finally:
            agent.cleanup()
