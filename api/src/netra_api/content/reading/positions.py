"""Reading-position navigation interface.

Ownership note: CLAUDE.md assigns *storing* the student's current
reading position to the Session service ("reading position/preferences
-> Session service in PostgreSQL"; see netra_api.session.state.
ReadingPosition). This module does not store position. It resolves
what a locator points to and what the next/previous locator is within
a SourceVersion's ReadingBlocks, which the Content/Ingestion service
does own. The Session service calls this to compute the target of
deterministic navigation commands (CLAUDE.md "Deterministic commands":
next/previous/repeat/"where am I") before persisting the new position
itself.

NavigationUnit is imported from netra_api.session.modes rather than
redefined here. It mirrors the navigation_unit enum in
shared/contracts/protocol/v1/client_to_server.schema.json, and a second
copy in this package would be free to drift from both the contract and
the Session service that supplies the value.
"""

from __future__ import annotations

from typing import Optional, Protocol
from uuid import UUID

from pydantic import BaseModel

from netra_api.session.modes import NavigationUnit


class BlockLocator(BaseModel):
    """Points at one sentence, or at a whole block when sentence_id is None."""

    source_version_id: UUID
    block_id: UUID
    sentence_id: Optional[UUID] = None


class ReadingPositionRepository(Protocol):
    """Typed contract for resolving and navigating locators within a SourceVersion.

    Read-only from the Content service's perspective; it never persists
    "the student is here" — see the module docstring.
    """

    def resolve(self, locator: BlockLocator) -> BlockLocator:
        """Validate that locator points at real content; return it unchanged.

        Raises when block_id/sentence_id does not exist in
        source_version_id.
        """
        ...

    def first(self, source_version_id: UUID) -> BlockLocator:
        ...

    def next(
        self, locator: BlockLocator, unit: NavigationUnit = NavigationUnit.SENTENCE
    ) -> Optional[BlockLocator]:
        """Locator of the next unit after the given one, or None at end of document.

        unit selects what "next" steps over. The protocol lets a client
        ask to move by sentence, block, heading, figure or equation
        (shared/contracts/protocol/v1/client_to_server.schema.json),
        and a resolver that only ever advanced one sentence would make
        three of those five silently behave as a different command.
        """
        ...

    def previous(
        self, locator: BlockLocator, unit: NavigationUnit = NavigationUnit.SENTENCE
    ) -> Optional[BlockLocator]:
        """Locator of the previous unit before the given one, or None at start."""
        ...
