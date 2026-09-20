"""U1: the upload and polled job-status routes of the C5 job contract.

Three routes, all scoped by the caller's bound session exactly like the
existing /v1/sessions routes:

- POST /v1/sessions/{session_id}/uploads       202 {"job": Job}
- GET  /v1/sessions/{session_id}/jobs/{job_id} 200 {"job": Job}
- GET  /v1/sessions/{session_id}/jobs          200 {"jobs": [Job, ...]}

No new table. ``SourceIngestionService`` already derives source and version
ids from an ``operation_key`` with uuid5, so a retransmitted upload rebuilds
the same identity; the job id is derived from the source the same way. That
keeps request_id replay, the title and created_at in the canonical rows that
already exist, and leaves the failure-reason column to M2 (I2) -- until it
lands, a failed version reports the contract's ``processing_failed``.

A job id is resolved only by scanning the caller's own sources, so a job
belonging to another account and an unknown job id are indistinguishable
from here: both raise AuthorizationError, and the route is never an
existence oracle (C5).
"""

from __future__ import annotations

import hashlib
from typing import Any, Optional
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from netra_api.content.job_status import (
    JobStatus,
    StageJobState,
    derive_job_status,
)
from netra_api.platform.auth_context import AuthContext, AuthenticatedPrincipal
from netra_api.platform.awaitables import maybe_await
from netra_api.platform.errors import (
    AuthorizationError,
    IdempotencyConflictError,
    InvalidRequestError,
    NetraError,
    ResourceUnavailableError,
)
from netra_api.transport.http.sessions import error_response
from netra_api.transport.websocket.dispatcher import TransportServices

MAX_JOBS_LISTED = 20
"""C5: the caller's most recent jobs, newest first, at most 20."""

UPLOAD_CONTENT_TYPE = "application/pdf"
_PDF_MAGIC = b"%PDF-"


class UploadRequest(BaseModel):
    """The multipart fields other than the file itself."""

    request_id: UUID
    title: str = Field(min_length=1, max_length=200)


def job_id_for(source_id: UUID) -> UUID:
    """One upload creates one source, so the job id derives from it.

    Deliberately not the source id and not derivable backwards by a client
    (C5: "Not a source id and not a provider id").
    """

    return uuid5(source_id, "netra:upload_job")


def _operation_key(account_id: UUID, request_id: UUID) -> str:
    """Replay identity for one logical upload.

    Account-scoped so one account's request_id can never resolve to another's
    source; SourceIngestionService rejects that case anyway, but it should not
    be reachable in the first place.
    """

    return f"upload:{account_id}:{request_id}"


def _object_key(account_id: UUID, request_id: UUID, content_hash: str) -> str:
    """Includes the content hash so a request_id reused for DIFFERENT bytes
    cannot overwrite the original upload's object. The conflict is only
    detected after the bytes are stored (the canonical row is the arbiter),
    so the two must never share a key; the rejected object is then unreferenced
    and expires with the bucket's lifecycle rule."""

    folder = uuid5(NAMESPACE_URL, _operation_key(account_id, request_id))
    return f"sources/{account_id}/{folder}/{content_hash}.pdf"


def _looks_like_a_pdf(data: bytes) -> bool:
    """Structural check only; the parser is the real authority.

    Rejecting here turns an unreadable upload into an immediate 422 instead of
    a job that fails minutes later, which is the difference the student hears.
    """

    return data.startswith(_PDF_MAGIC)


async def _stage_jobs(session_factory: Any, source_version_id: UUID) -> list[StageJobState]:
    """Pipeline rows for one version, for the optional progress stage.

    Read-only and best effort: a status response must not fail because the
    progress detail could not be read.
    """

    from netra_api.db.models import JobRow

    try:
        async with session_factory() as session:
            rows = (await session.execute(
                select(JobRow).where(JobRow.operation_key.like(f"%:{source_version_id}"))
            )).scalars().all()
            return [StageJobState(job_type=row.job_type, status=row.status,
                                  attempts=row.attempts, updated_at=row.updated_at)
                    for row in rows]
    except Exception:  # noqa: BLE001 - progress detail is never worth a failed response
        return []


async def _existing_source(sources: Any, auth: AuthContext, operation_key: str) -> bool:
    """Whether this operation key already created a source for this account."""

    from netra_api.content.sources.ingestion import source_id_for

    try:
        await maybe_await(sources.get_source(auth, source_id_for(operation_key)))
    except NetraError:
        return False
    return True


async def _job_for_source(sources: Any, session_factory: Any, auth: AuthContext,
                          source: Any) -> Optional[JobStatus]:
    """Derive one source's upload job, or None if it has no first version."""

    versions = await maybe_await(sources.list_versions(auth, source.source_id))
    first = next((v for v in versions if v.version_number == 1), None)
    if first is None:
        return None
    stage_jobs = await _stage_jobs(session_factory, first.source_version_id) if session_factory else []
    return derive_job_status(
        job_id=job_id_for(source.source_id), title=source.title, version=first,
        stage_jobs=stage_jobs, created_at=source.created_at,
    )


async def create_upload(services: TransportServices, sources: Any, ingestion: Any, storage: Any,
                        session_factory: Any, max_body_bytes: Optional[int],
                        principal: AuthenticatedPrincipal, session_id: UUID,
                        fields: dict[str, Any], file_bytes: bytes) -> tuple[int, dict[str, Any]]:
    """POST /v1/sessions/{session_id}/uploads."""

    try:
        if sources is None or ingestion is None or storage is None:
            raise ResourceUnavailableError("source ingestion is not registered")
        if max_body_bytes is None:
            # D-UPLOAD-SIZE: no invented default.
            raise ResourceUnavailableError("maximum upload size is not configured")

        try:
            request = UploadRequest.model_validate(fields)
        except ValidationError as exc:
            field = ".".join(str(p) for p in exc.errors()[0].get("loc", ())) if exc.errors() else None
            raise InvalidRequestError("upload fields are invalid", field=field) from exc

        if not file_bytes:
            raise InvalidRequestError("the uploaded file is empty", field="file")
        if len(file_bytes) > max_body_bytes:
            raise InvalidRequestError(
                f"the uploaded file exceeds the {max_body_bytes}-byte limit", field="file")
        if not _looks_like_a_pdf(file_bytes):
            raise InvalidRequestError("the uploaded file is not a readable PDF", field="file")

        auth = await services.identity.resolve_auth_context(principal, session_id, uuid4())
        operation_key = _operation_key(auth.account_id, request.request_id)
        content_hash = hashlib.sha256(file_bytes).hexdigest()
        object_key = _object_key(auth.account_id, request.request_id, content_hash)

        # C5 distinguishes a first upload (202, work scheduled) from a
        # retransmission of the same one (200, nothing new happened).
        existing = await _existing_source(sources, auth, operation_key)

        # Bytes first: the canonical row must never reference an object that is
        # not there. put_object is idempotent for the same key, so a replay
        # rewrites identical bytes rather than creating a second object.
        await maybe_await(storage.put_object(object_key, file_bytes, UPLOAD_CONTENT_TYPE))

        created = await maybe_await(ingestion.create_source_and_schedule_parse(
            auth, request.title, object_key, UPLOAD_CONTENT_TYPE, content_hash,
            operation_key=operation_key))

        source = await maybe_await(sources.get_source(auth, created.source_id))
        version = await maybe_await(sources.get_version(auth, created.source_version_id))
        replayed = version.content_hash is not None and version.content_hash != content_hash
        if replayed:
            # Same request_id, different bytes: C5 calls this a conflict rather
            # than silently keeping either version.
            raise IdempotencyConflictError(str(request.request_id))

        stage_jobs = await _stage_jobs(session_factory, version.source_version_id) if session_factory else []
        job = derive_job_status(job_id=job_id_for(created.source_id), title=source.title,
                                version=version, stage_jobs=stage_jobs, created_at=source.created_at)
    except NetraError as exc:
        return error_response(exc)
    return (200 if existing else 202), {"job": job.public()}


async def get_job(services: TransportServices, sources: Any, session_factory: Any,
                  principal: AuthenticatedPrincipal, session_id: UUID,
                  job_id: UUID) -> tuple[int, dict[str, Any]]:
    """GET /v1/sessions/{session_id}/jobs/{job_id}."""

    try:
        if sources is None:
            raise ResourceUnavailableError("source storage is not registered")
        auth = await services.identity.resolve_auth_context(principal, session_id, uuid4())
        for source in await maybe_await(sources.list_sources(auth)):
            if job_id_for(source.source_id) != job_id:
                continue
            job = await _job_for_source(sources, session_factory, auth, source)
            if job is not None:
                return 200, {"job": job.public()}
        # Unknown and someone else's are the same answer, so this route cannot
        # be used to discover that a job exists.
        raise AuthorizationError("job is not accessible")
    except NetraError as exc:
        return error_response(exc)


async def list_jobs(services: TransportServices, sources: Any, session_factory: Any,
                    principal: AuthenticatedPrincipal,
                    session_id: UUID) -> tuple[int, dict[str, Any]]:
    """GET /v1/sessions/{session_id}/jobs."""

    try:
        if sources is None:
            raise ResourceUnavailableError("source storage is not registered")
        auth = await services.identity.resolve_auth_context(principal, session_id, uuid4())
        jobs = []
        for source in await maybe_await(sources.list_sources(auth)):
            job = await _job_for_source(sources, session_factory, auth, source)
            if job is not None:
                jobs.append(job)
        jobs.sort(key=lambda j: j.created_at, reverse=True)
    except NetraError as exc:
        return error_response(exc)
    return 200, {"jobs": [job.public() for job in jobs[:MAX_JOBS_LISTED]]}


__all__ = ["MAX_JOBS_LISTED", "create_upload", "get_job", "job_id_for", "list_jobs"]
