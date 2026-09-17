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

from netra_api.multimedia.video.evidence_resolution import MomentEvidence
from netra_api.multimedia.video.models import VideoEvidenceItem
from netra_api.multimedia.video.readiness import VideoCapabilityReport
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

    async def capability_report(
        self, auth: AuthContext, video_id: UUID
    ) -> VideoCapabilityReport:
        """Both readiness verdicts for one video, checked independently.

        Implementations build this from
        netra_api.multimedia.video.readiness.assess_playback and
        assess_analysis, supplying each fact from the service that owns
        it. They must not derive one verdict from the other.
        """
        ...

    async def evidence_at_player_time(
        self,
        auth: AuthContext,
        video_id: UUID,
        captured_player_time_ms: int,
    ) -> MomentEvidence:
        """Evidence around the player position M5 reported for this video.

        captured_player_time_ms is the client's actual playback position,
        recorded when pause-and-describe paused the lecture. It is an
        argument rather than something the service looks up because the
        player owns it: the server's last sent audio offset is not where
        the student is, and a stale value silently answers about the
        wrong moment.

        Implementations must authorize video_id, then call
        netra_api.multimedia.video.evidence_resolution.resolve_moment_evidence
        over already-authorized items. A TRANSCRIPT_ONLY result is a
        normal return value, not an error: the caller is expected to
        change strategy on it.
        """
        ...
