"""Non-durable in-process identity repository.

For unit tests and labelled fixture journeys only. Production composition
(netra_api.bootstrap) never registers it; a restart forgets every record, so
using it in a deployment would silently lose access decisions.
"""

from __future__ import annotations

import asyncio
from typing import Optional
from uuid import UUID

from netra_api.identity.models import Account, DeviceAccess, SessionBinding, StoredCredential
from netra_api.platform.errors import IdempotencyConflictError


class InMemoryIdentityRepository:
    def __init__(self) -> None:
        self.accounts: dict[UUID, Account] = {}
        self.devices: dict[tuple[UUID, UUID], DeviceAccess] = {}
        self.credentials: dict[str, StoredCredential] = {}
        self.bindings: dict[UUID, SessionBinding] = {}
        self._lock = asyncio.Lock()

    async def get_account(self, account_id: UUID) -> Optional[Account]:
        return self.accounts.get(account_id)

    async def get_device(self, account_id: UUID, device_id: UUID) -> Optional[DeviceAccess]:
        return self.devices.get((account_id, device_id))

    async def find_credential(self, token_sha256: str) -> Optional[StoredCredential]:
        return self.credentials.get(token_sha256)

    async def get_session_binding(self, session_id: UUID) -> Optional[SessionBinding]:
        return self.bindings.get(session_id)

    async def create_session_binding(self, binding: SessionBinding) -> SessionBinding:
        async with self._lock:
            if binding.session_id in self.bindings:
                raise IdempotencyConflictError(str(binding.session_id))
            self.bindings[binding.session_id] = binding
            return binding

    # Fixture helpers -----------------------------------------------------

    def add_account(self, account: Account) -> None:
        self.accounts[account.account_id] = account

    def add_device(self, device: DeviceAccess) -> None:
        self.devices[(device.account_id, device.device_id)] = device

    def add_credential(self, credential: StoredCredential) -> None:
        self.credentials[credential.token_sha256] = credential
