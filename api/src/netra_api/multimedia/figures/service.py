"""Figure-processing bounded tool interface.

Not an agent (CLAUDE.md "Architecture: only two agents" explicitly
lists figure processing as NOT an agent). This is the interface the
Coordinator/Tutor's tool layer calls through; it must never receive the
Coordinator's full conversation history and must never mutate learning
state. Concrete figure description generation happens in
netra_worker.jobs.multimedia.figures and is persisted before this
service serves it back out (mirrors the ingestion pipeline's
write-then-serve split).
"""

from __future__ import annotations

from typing import List, Protocol
from uuid import UUID

from netra_api.multimedia.figures.models import FigureDescription
from netra_api.platform.auth_context import AuthContext


class FigureService(Protocol):
    """Typed contract for retrieving already-processed figure descriptions.

    Implementations must authorize source_version_id against
    auth.account_id and must call
    netra_api.multimedia.figures.evidence.authorize_figure_evidence on
    every FigureDescription.reference before returning it.
    """

    async def get_figure(
        self, auth: AuthContext, source_version_id: UUID, figure_index: int
    ) -> FigureDescription:
        ...

    async def list_figures(
        self, auth: AuthContext, source_version_id: UUID
    ) -> List[FigureDescription]:
        ...
