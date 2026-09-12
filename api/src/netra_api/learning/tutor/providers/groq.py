"""Groq provider adapter interface for the Tutor.

No network calls happen here yet, and the groq SDK is not imported
(CLAUDE.md "Provider adapters"). docs/architecture/runtime-baseline.md
pins groq==1.7.0 as an approved-but-not-yet-installed dependency and
states "The Groq Tutor model remains openai/gpt-oss-120b." Business
logic must depend on this interface and the typed TutorModelDecision it
returns, never on the Groq SDK's own response objects, so the provider
can be swapped without touching netra_api.learning.tutor.agent.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

TUTOR_MODEL_ID = "openai/gpt-oss-120b"
"""Explicit, versioned model id (CLAUDE.md "Provider adapters": "No
`latest` model aliases in release configuration"; see also
docs/architecture/runtime-baseline.md: "The Groq Tutor model remains
openai/gpt-oss-120b")."""


class GroqTutorModelConfig(BaseModel):
    """Explicit, versioned model configuration. No 'latest' aliases."""

    model_id: str = TUTOR_MODEL_ID
    temperature: float = 0.3
    max_output_tokens: int = 1024


class TutorModelDecision(BaseModel):
    """Provider-agnostic shape the Tutor reasons over.

    An adapter implementation populates this from a Groq chat
    completion; business logic never touches the raw SDK response
    object.
    """

    raw_text: str
    finish_reason: str


class GroqTutorProvider(Protocol):
    """Adapter boundary for calling Groq on the Tutor's behalf.

    A concrete implementation makes the network call; this interface
    only fixes the shape callers depend on. No Groq SDK call is
    implemented here.
    """

    async def decide(self, config: GroqTutorModelConfig, prompt: str) -> TutorModelDecision:
        ...
