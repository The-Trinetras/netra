"""Source and source-version persistence interface.

PostgreSQL is authoritative (CLAUDE.md "Data authority"). Agents never
open a database connection themselves (CLAUDE.md "Agents never own
database connections"); the Coordinator's source-selection tool calls
through a concrete implementation of this Protocol instead. A concrete
PostgreSQL-backed implementation is added alongside the sources/
source_versions migrations, not here.
"""

from __future__ import annotations

from typing import List, Optional, Protocol
from uuid import UUID

from netra_api.content.sources.models import Source, SourceVersion
from netra_api.platform.auth_context import AuthContext


class SourceRepository(Protocol):
    """Typed contract for reading sources and managing version activation."""

    def get_source(self, auth: AuthContext, source_id: UUID) -> Source:
        """Raise netra_api.platform.errors.AuthorizationError if source_id
        is not owned by auth.account_id."""
        ...

    def list_sources(self, auth: AuthContext) -> List[Source]:
        ...

    def list_versions(self, auth: AuthContext, source_id: UUID) -> List[SourceVersion]:
        ...

    def get_version(self, auth: AuthContext, source_version_id: UUID) -> SourceVersion:
        """Resolve one source version by its own id.

        The reverse lookup every version check needs. Evidence,
        multimedia references and session reading positions all carry a
        source_version_id and nothing else, so without this there is no
        way to reach the owning source, ask whether that version is still
        active, or compare it against the version a session is pinned to.

        Raises netra_api.platform.errors.AuthorizationError when the
        version's source is not owned by auth.account_id, so an
        identifier leaked from a derived projection cannot be turned into
        a source record (CLAUDE.md: "Validate vector references against
        PostgreSQL before supplying evidence to models")."""
        ...

    def get_active_version(self, auth: AuthContext, source_id: UUID) -> Optional[SourceVersion]:
        ...

    def create_version(self, auth: AuthContext, source_id: UUID) -> SourceVersion:
        """Create the next pending SourceVersion for source_id."""
        ...

    def activate_version(
        self,
        auth: AuthContext,
        source_id: UUID,
        source_version_id: UUID,
        expected_version_number: int,
    ) -> SourceVersion:
        """Pin source_id's active version, enforcing optimistic concurrency.

        expected_version_number guards against activating against a
        stale view of the source (mirrors the session optimistic-version
        pattern in netra_api.platform.idempotency.check_expected_version).
        Deactivating any previously active version and activating
        source_version_id happens in one PostgreSQL transaction.
        """
        ...
