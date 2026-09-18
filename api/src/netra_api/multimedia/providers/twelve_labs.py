"""Twelve Labs provider adapter interfaces — Marengo and Pegasus.

CLAUDE.md "Provider adapters": external providers sit behind
interfaces/adapters; business logic must not depend on a provider SDK's
response objects directly (see also
docs/architecture/runtime-baseline.md, which pins the twelvelabs SDK at
1.3.4 as an approved-but-not-yet-installed dependency). No twelvelabs
SDK call is implemented here and the SDK is not imported — only
Netra-owned models and Protocol interfaces, so provider wiring can be
added later without touching netra_worker.jobs.multimedia.video call
sites. No network calls, no API keys, and no provider is wired up in
this scaffold. The registered Twelve Labs adapters (indexing, Pegasus
description, Marengo retrieval) are in
netra_api.multimedia.providers.twelve_labs_client.

Marengo is Twelve Labs' multimodal embedding model (video/text/audio/
image -> a shared vector space for semantic search). Pegasus is Twelve
Labs' video-language model (summarization, open-ended Q&A, generating
text grounded in a video's visual and audio content). Netra uses
Marengo for video evidence *search* and Pegasus for video evidence
*description/summarization generation*; keeping them as two adapters,
mirroring Twelve Labs' own model split, lets either be replaced
independently (CLAUDE.md "provider adapters must be replaceable").
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional, Protocol
from uuid import UUID

from pydantic import BaseModel


class TwelveLabsVideoRef(BaseModel):
    """A video already indexed with Twelve Labs, addressed by Netra's own IDs.

    provider_video_id is Twelve Labs' own identifier for the indexed
    asset; Netra code must key everything else off source_version_id so
    swapping providers never changes how evidence is addressed elsewhere
    in the system.
    """

    source_version_id: UUID
    provider_video_id: str


class MarengoEmbeddingScope(str, Enum):
    VIDEO = "video"
    TEXT = "text"
    AUDIO = "audio"
    IMAGE = "image"


class MarengoEmbedding(BaseModel):
    """One embedding vector produced by Marengo for a scope/segment."""

    scope: MarengoEmbeddingScope
    start_ms: Optional[int] = None
    end_ms: Optional[int] = None
    vector: List[float]


class MarengoProvider(Protocol):
    """Typed contract for the Marengo multimodal-embedding adapter.

    Embeddings are a derived projection, same as
    netra_api.content.providers.pinecone.VectorIndexProvider — a failed
    Marengo call must never roll back an already-committed PostgreSQL
    mutation (CLAUDE.md "Data authority").
    """

    async def embed_video(self, video: TwelveLabsVideoRef) -> List[MarengoEmbedding]:
        ...

    async def embed_text(self, query_text: str) -> MarengoEmbedding:
        """Embed free text into the same space as embed_video's output,
        for semantic video search."""
        ...


class PegasusGenerationKind(str, Enum):
    SUMMARY = "summary"
    SCENE_DESCRIPTION = "scene_description"
    OPEN_ENDED_QA = "open_ended_qa"


class PegasusGenerationRequest(BaseModel):
    video: TwelveLabsVideoRef
    kind: PegasusGenerationKind
    prompt: Optional[str] = None
    """Required for OPEN_ENDED_QA; ignored otherwise."""
    start_ms: Optional[int] = None
    end_ms: Optional[int] = None
    """Restrict generation to a time range; None covers the whole video."""


class PegasusGenerationResult(BaseModel):
    """Raw Pegasus output — untrusted, provider-generated text.

    This is NOT Evidence (see netra_api.multimedia.evidence) yet: a
    caller must wrap it into a VideoEvidenceReference/VideoEvidenceItem
    and pass it through resolve_and_authorize before it can be cited
    (CLAUDE.md "Models may reference evidence IDs. They must not
    construct authoritative evidence text themselves.").
    """

    text: str
    start_ms: Optional[int] = None
    end_ms: Optional[int] = None


class PegasusProvider(Protocol):
    """Typed contract for the Pegasus video-language-model adapter."""

    async def generate(self, request: PegasusGenerationRequest) -> PegasusGenerationResult:
        ...
