"""Diagram-structure-extraction bounded tool interface.

Not an agent (CLAUDE.md "Architecture: only two agents"). Concrete
layered diagram extraction happens in
netra_worker.jobs.multimedia.diagrams and is persisted before this
service serves it back out; mirrors
netra_api.multimedia.figures.service.FigureService.
"""

from __future__ import annotations

from typing import List, Protocol
from uuid import UUID

from netra_api.multimedia.diagrams.models import DiagramStructure
from netra_api.platform.auth_context import AuthContext


class DiagramExtractionService(Protocol):
    """Typed contract for retrieving already-extracted diagram structures.

    Implementations must authorize source_version_id against
    auth.account_id and must call
    netra_api.multimedia.figures.evidence.authorize_figure_evidence on
    every DiagramStructure.reference before returning it.
    """

    async def get_diagram(
        self, auth: AuthContext, source_version_id: UUID, figure_index: int
    ) -> DiagramStructure:
        ...

    async def list_diagrams(
        self, auth: AuthContext, source_version_id: UUID
    ) -> List[DiagramStructure]:
        ...
