"""Video-evidence-processing bounded tool interface.

Not an agent (CLAUDE.md "Architecture: only two agents"). Concrete
video evidence extraction (transcript alignment, visual description,
Pegasus Q&A) happens in netra_worker.jobs.multimedia.video via the
Twelve Labs provider adapters (see
netra_api.multimedia.providers.twelve_labs) and is persisted before
this service serves it back out.
"""

from __future__ import annotations

from typing import List, Protocol
from uuid import UUID

from netra_api.multimedia.video.models import VideoEvidenceItem
from netra_api.platform.auth_context import AuthContext


class VideoEvidenceService(Protocol):
    """Typed contract for retrieving already-processed video evidence.

    Implementations must authorize source_version_id against
    auth.account_id and must call
    netra_api.multimedia.evidence.resolve_and_authorize on every
    VideoEvidenceItem.reference before returning it.
    """

    async def list_evidence(
        self, auth: AuthContext, source_version_id: UUID
    ) -> List[VideoEvidenceItem]:
        ...

    async def search_evidence(
        self, auth: AuthContext, source_version_id: UUID, query_text: str
    ) -> List[VideoEvidenceItem]:
        """Semantic search within one video's derived evidence (e.g. backed
        by a Marengo embedding query — see
        netra_api.multimedia.providers.twelve_labs.MarengoProvider)."""
        ...
