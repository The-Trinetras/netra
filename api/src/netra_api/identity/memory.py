"""Non-durable in-process identity repository.

For unit tests and labelled fixture journeys only. Production composition
(netra_api.bootstrap) never registers it; a restart forgets every record, so
using it in a deployment would silently lose access decisions.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID, uuid4

from netra_api.identity.access_codes import AccessCodeRecord, ExchangeDecision, decide_exchange
from netra_api.identity.models import Account, DeviceAccess, SessionBinding, StoredCredential
from netra_api.platform.errors import IdempotencyConflictError


class InMemoryIdentityRepository:
    def __init__(self) -> None:
        self.accounts: dict[UUID, Account] = {}
        self.devices: dict[tuple[UUID, UUID], DeviceAccess] = {}
        self.credentials: dict[str, StoredCredential] = {}
        self.bindings: dict[UUID, SessionBinding] = {}
        self.access_codes: dict[str, AccessCodeRecord] = {}
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

    async def issue_access_code(self, record: AccessCodeRecord) -> None:
        async with self._lock:
            self.accounts.setdefault(record.account_id, Account(account_id=record.account_id))
            self.access_codes[record.code_sha256] = record

    async def exchange_access_code(
        self, code_sha256: str, request_id: UUID, token_sha256: str, now: datetime
    ) -> Optional[datetime]:
        async with self._lock:
            record = self.access_codes.get(code_sha256)
            account = self.accounts.get(record.account_id) if record else None
            if record is None or account is None or not account.is_active:
                return None
            decision = decide_exchange(record, request_id, now)
            if decision is ExchangeDecision.REJECT:
                return None
            if decision is ExchangeDecision.REPLAY:
                previous = next(c for c in self.credentials.values() if c.credential_id == record.credential_id)
                self.credentials[previous.token_sha256] = previous.model_copy(update={"revoked_at": now})
                device_id = previous.device_id
            else:
                device_id = uuid4()
                self.add_device(DeviceAccess(device_id=device_id, account_id=record.account_id))
            credential = StoredCredential(
                credential_id=uuid4(), token_sha256=token_sha256, account_id=record.account_id, device_id=device_id,
                issued_at=now, expires_at=now + timedelta(seconds=record.credential_lifetime_seconds),
            )
            self.credentials[token_sha256] = credential
            self.access_codes[code_sha256] = record.model_copy(update={
                "exchanged_at": record.exchanged_at or now, "exchange_request_id": request_id,
                "credential_id": credential.credential_id,
            })
            return credential.expires_at

    # Fixture helpers -----------------------------------------------------

    def add_account(self, account: Account) -> None:
        self.accounts[account.account_id] = account

    def add_device(self, device: DeviceAccess) -> None:
        self.devices[(device.account_id, device.device_id)] = device

    def add_credential(self, credential: StoredCredential) -> None:
        self.credentials[credential.token_sha256] = credential
