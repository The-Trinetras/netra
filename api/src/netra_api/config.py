"""API runtime configuration (environment variables prefixed ``NETRA_``).

Only values the repository has approved or that are pure wiring switches live
here. Unapproved product values — result-set retention, speech quota, total
binary frame size, credential issuance — have NO defaults: leaving them unset
disables the dependent capability explicitly instead of inventing policy.

Secrets are read from the environment by the process only; they are never
logged or returned by health endpoints.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # A blank line such as ``NETRA_GEMINI_API_KEY=`` in an env file means "not
    # set", not "set to an empty key" (which used to stop the API at startup).
    model_config = SettingsConfigDict(env_prefix="NETRA_", extra="ignore", env_ignore_empty=True)

    database_url: Optional[str] = None
    """postgresql+asyncpg URL for application access. Unset -> identity and
    session persistence are unavailable and every request fails closed."""

    auth_mode: Literal["unconfigured", "stored_credential"] = "unconfigured"
    """stored_credential verifies previously issued bearer credentials stored as
    SHA-256 digests (requires database_url). Issuance remains undecided."""

    result_set_ttl_seconds: Optional[int] = Field(default=None, ge=1)
    """Retention for stored result sets. Unapproved value: unset disables them."""

    trace_to_log: bool = True
    """Operational log of structured action/evidence events (not AX export)."""

    tracing_mode: Literal["off", "local", "ax"] = "off"
    """Span tracing. ``ax`` needs a reviewed OTLP exporter that is not in this
    build (pending OpenTelemetry pins via M2); requesting it records a
    configuration error and tracing stays off. It never blocks boot."""

    tracing_shutdown_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    """Bound on the final flush at orderly shutdown; implementation bound."""

    gemini_api_key: Optional[SecretStr] = None
    """Coordinator model adapter (google-genai), used only when
    openrouter_api_key is unset. The same variable also serves embeddings
    (ContentSettings). With neither key no Coordinator is registered and turns
    fail with PROVIDER_UNAVAILABLE; navigation still works."""

    groq_api_key: Optional[SecretStr] = None
    """Tutor model adapter (groq), used only when openrouter_api_key is unset.
    With neither key no Tutor is registered and delegation fails closed."""

    openrouter_api_key: Optional[SecretStr] = None
    """OpenRouter (the Agent-a-thon key). When set it runs both agents with the
    same approved models, even if Gemini or Groq keys are also set (decision
    D-AGENT, docs/team/integration-status.md). Read from
    NETRA_OPENROUTER_API_KEY only: an exported bare OPENROUTER_API_KEY must not
    quietly turn on paid model calls in the API or its tests."""

    openrouter_coordinator_model: str = "google/gemini-3.8-flash"
    """The approved Coordinator pin under its OpenRouter id. Pinned, never a
    ``~latest`` alias or a ``:batch`` variant."""

    openrouter_tutor_model: str = "openai/gpt-oss-120b"
    """The approved Tutor model; OpenRouter uses the same id as Groq."""

    elevenlabs_api_key: Optional[SecretStr] = None
    """Server speech (ElevenLabs). Speech is registered only when this, the model
    and voice ids and a database (for the quota ledger) are all set; otherwise
    replies are text only."""

    elevenlabs_model_id: Optional[str] = None
    elevenlabs_voice_id: Optional[str] = None
    elevenlabs_output_format: str = "mp3_44100_128"
    """Sent as audio/mpeg (INT-11b); non-mp3 formats are refused at startup."""

    elevenlabs_timeout_seconds: float = Field(default=10.0, gt=0, le=20)

    speech_daily_characters: int = Field(default=20_000, ge=1)
    """D-QUOTA: synthesized characters per student per UTC day."""

    model_request_timeout_seconds: float = Field(default=20.0, gt=0, le=20)
    """Transport timeout for one model request. The Coordinator additionally
    bounds every call by the turn's remaining time; this never exceeds the
    approved 20-second answer deadline."""
