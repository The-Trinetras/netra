"""Gemini provider adapter interface for the Coordinator.

No network calls happen here yet. Business logic must depend on this
interface and the typed ModelDecision it returns, never on the Gemini
SDK's own response objects (CLAUDE.md "Provider adapters"), so the
provider can be swapped without touching coordinator/router.py.
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field


class GeminiModelConfig(BaseModel):
    """Explicit, versioned model configuration. No 'latest' aliases in release config."""

    model_id: str
    """Pin an explicit, versioned model id, e.g. "gemini-2.5-pro-001". Never "latest"."""
    temperature: float = 0.2
    max_output_tokens: int = 1024


COORDINATOR_MODEL_ID = "gemini-3.8-flash"
"""Approved configured pin (2026-09-12), not an independently verified
provider availability claim: no live Gemini call has been made to confirm
this model id currently exists on the provider's API. runtime-baseline.md
previously left "exact Coordinator Gemini model ID" as
Pending approved contract/policy decision; this is that decision,
recorded as configuration rather than hardcoded at each call site so a
future change stays a one-line edit here."""


DEFAULT_COORDINATOR_MODEL_CONFIG = GeminiModelConfig(model_id=COORDINATOR_MODEL_ID)


class ToolCallRequest(BaseModel):
    """A tool the model asked to call. A request, never an execution.

    CLAUDE.md: "Agents request actions through bounded services/tools"
    and coordinator.md: "Keep provider automatic tool execution from
    bypassing Netra's tool gateway." Provider SDKs will happily run a
    function call themselves and hand back the result; returning the
    request as inert data instead means every call still has to go
    through netra_api.coordinator.tool_registry, where it is authorized,
    budget-counted and audited.

    An adapter that lets the SDK execute a tool directly violates that
    boundary, however convenient the SDK makes it.
    """

    tool_name: str
    arguments: dict[str, Any]
    """Model-supplied arguments. Untrusted input: validate against the
    tool's own schema, and never read identity or authorization from
    here (CLAUDE.md: "Runtime injects trusted identity; model arguments
    contain no authority override")."""


class ModelDecision(BaseModel):
    """Provider-agnostic shape the Coordinator reasons over.

    An adapter implementation populates this from a Gemini response;
    business logic never touches the raw SDK response object.
    """

    raw_text: str
    finish_reason: str
    tool_calls: list[ToolCallRequest] = Field(default_factory=list)
    """Tools the model requested, in the order requested. Empty when the
    model answered directly."""


class ToolSpec(BaseModel):
    """Provider-neutral description of one permitted tool offered to the model."""

    name: str
    description: str
    input_schema: dict[str, Any]


class GeminiCoordinatorProvider(Protocol):
    """Adapter boundary for calling Gemini on the Coordinator's behalf.

    Implementations must offer ``tools`` as declarations only, disable any
    SDK automatic function execution, and return requested calls as inert
    ToolCallRequest data. The runtime counts the attempt before calling.
    """

    async def decide(self, config: GeminiModelConfig, prompt: str, tools: list[ToolSpec]) -> ModelDecision:
        ...


class UnavailableGeminiProvider:
    """Explicitly unavailable adapter.

    google-genai==2.21.0 is pinned but not installed in the verified
    environment, and live provider calls are not authorized, so no network
    adapter ships in this build. Every call fails with PROVIDER_UNAVAILABLE;
    deterministic navigation keeps working without it.
    """

    async def decide(self, config: GeminiModelConfig, prompt: str, tools: list[ToolSpec]) -> ModelDecision:
        from netra_api.platform.errors import ProviderUnavailableError

        raise ProviderUnavailableError("the Coordinator model adapter is not available in this build")
