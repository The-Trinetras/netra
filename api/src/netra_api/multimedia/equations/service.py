"""Equation-processing bounded tool interface.

Not an agent (CLAUDE.md "Architecture: only two agents"). Concrete
equation-tree extraction happens in
netra_worker.jobs.multimedia.equations and is persisted before this
service serves it back out.
"""

from __future__ import annotations

from typing import List, Protocol
from uuid import UUID

from netra_api.multimedia.equations.models import EquationTree
from netra_api.platform.auth_context import AuthContext


class EquationService(Protocol):
    """Typed contract for retrieving already-processed equation trees.

    Implementations must authorize source_version_id against
    auth.account_id and must call
    netra_api.multimedia.evidence.resolve_and_authorize on every
    EquationTree.reference before returning it.
    """

    async def get_equation(
        self, auth: AuthContext, source_version_id: UUID, equation_index: int
    ) -> EquationTree:
        ...

    async def list_equations(
        self, auth: AuthContext, source_version_id: UUID
    ) -> List[EquationTree]:
        ...
