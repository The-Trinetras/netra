"""Typed, bounded tool-call interface for the Coordinator.

The Coordinator never accesses PostgreSQL, Neo4j, or Pinecone directly
(CLAUDE.md "Agents never own database connections"). Every side effect
goes through a registered ToolDefinition whose invoker is owned by the
service the tool belongs to (content, learning, session, ...), so tool
calls stay auditable and budget-tracked (see coordinator/limits.py).
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

from netra_api.platform.auth_context import AuthContext


class ToolDefinition(BaseModel):
    """Describes one bounded tool the Coordinator may call."""

    name: str
    description: str


class ToolInvoker(Protocol):
    """Executes a registered tool call.

    Concrete implementations live in the service each tool belongs to,
    never inside coordinator/.
    """

    async def __call__(self, auth: AuthContext, payload: BaseModel) -> BaseModel:
        ...


class ToolRegistry:
    """Maps tool names to their definition and invoker.

    Registration happens at application startup from each owning
    service; the Coordinator only ever looks tools up by name here.
    """

    def __init__(self) -> None:
        self._definitions: dict[str, ToolDefinition] = {}
        self._invokers: dict[str, ToolInvoker] = {}

    def register(self, definition: ToolDefinition, invoker: ToolInvoker) -> None:
        if definition.name in self._definitions:
            raise ValueError(f"tool {definition.name!r} is already registered")
        self._definitions[definition.name] = definition
        self._invokers[definition.name] = invoker

    def get_definition(self, name: str) -> ToolDefinition:
        return self._definitions[name]

    def get_invoker(self, name: str) -> ToolInvoker:
        return self._invokers[name]

    def list_tools(self) -> list[ToolDefinition]:
        return list(self._definitions.values())
