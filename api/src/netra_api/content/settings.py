"""M2 content, retrieval and worker-runtime configuration.

Kept separate from M1's composition ``netra_api.config.Settings`` so each
owner can change its own configuration without editing the other's file.
Both read environment variables with the same ``NETRA_`` prefix and neither
reads a ``.env`` file implicitly; secrets come from the process environment.

The database URL is deliberately NOT duplicated here: the application and
worker both use ``Settings.database_url`` (unset -> persistence fails closed).

Values below are implementation bounds of the M2 pipeline (batch sizes,
chunk token budgets, candidate counts). Embedding model/dimension and the
Pinecone index are pinned configuration: the pipeline refuses to mix a
different embedding specification with stored vectors instead of adapting.
"""

from __future__ import annotations

from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ContentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NETRA_", extra="ignore")

    # Application database pool (URL comes from netra_api.config.Settings)
    database_pool_size: int = Field(default=5, ge=1)

    # Embeddings (canonical vectors are stored in PostgreSQL with their spec)
    embedding_model: str = "gemini-embedding-001"
    embedding_dimension: int = Field(default=1536, ge=1)
    gemini_api_key: Optional[str] = None
    gemini_embedding_model: str = "gemini-embedding-001"
    gemini_embedding_dimension: int = Field(default=1536, ge=1)
    gemini_embedding_batch_size: int = Field(default=100, ge=1)

    # Pinecone projection (rebuildable; never authoritative)
    pinecone_api_key: Optional[str] = None
    pinecone_index_name: Optional[str] = None
    pinecone_namespace: str = "netra"
    pinecone_upsert_batch_size: int = Field(default=100, ge=1)

    # Worker runtime
    parse_workers: int = Field(default=3, ge=0)
    block_workers: int = Field(default=3, ge=0)
    embed_workers: int = Field(default=2, ge=0)
    projection_workers: int = Field(default=3, ge=0)
    activation_workers: int = Field(default=1, ge=0)
    worker_lease_duration_seconds: int = Field(default=60, ge=5)
    worker_poll_interval_seconds: float = Field(default=1.0, gt=0)
    outbox_lease_duration_seconds: int = Field(default=60, ge=5)

    # Uploads (C5). No default: the maximum upload size is an approved
    # operational decision (D-UPLOAD-SIZE), so uploads answer 503 until it is
    # configured rather than the server inventing a limit.
    upload_max_body: Optional[int] = Field(default=None, ge=1)

    # Private source storage
    s3_bucket: Optional[str] = None
    aws_region: Optional[str] = None
    storage_provider: str = "s3"
    local_fixture_root: Optional[str] = None

    # Structure-aware chunking
    chunk_target_tokens: int = Field(default=500, ge=1)
    chunk_min_tokens: int = Field(default=350, ge=1)
    chunk_max_tokens: int = Field(default=700, ge=1)
    chunk_overlap_tokens: int = Field(default=64, ge=0)

    # Hybrid retrieval
    vector_top_k: int = Field(default=20, ge=1)
    fts_top_k: int = Field(default=20, ge=1)
    rrf_k: int = Field(default=60, ge=1)
    fusion_top_k: int = Field(default=12, ge=1)
    rerank_top_k: int = Field(default=12, ge=1)
    reranker_model_id: str = "BAAI/bge-reranker-v2-m3"
    reranker_enabled: bool = False
    reranker_batch_size: int = Field(default=4, ge=1, le=12)
    reranker_device: str = "cpu"
    reranker_max_concurrency: int = Field(default=1, ge=1, le=4)
    final_evidence_min: int = Field(default=4, ge=1)
    final_evidence_max: int = Field(default=6, ge=1)

    # Local OCR fallback. Default resolves "tesseract" on PATH (EC2/Compose);
    # set NETRA_TESSERACT_EXECUTABLE for a Windows developer install.
    tesseract_executable: str = "tesseract"
    ocr_language: str = "eng"
    ocr_timeout_seconds: float = Field(default=30.0, gt=0)
    ocr_dpi: int = Field(default=300, ge=72)

    # Operational logs/metrics (not AX; spans go through M1's Tracer)
    log_format: str = "json"
    metrics_enabled: bool = True

    # Video analysis (M3-PIN-1). TwelveLabsSettings requires every model pin
    # and none is defaulted here: the pins must match the models the index was
    # actually created with, and guessing one produces evidence attributed to
    # a model that never ran. Unset leaves video analysis unregistered rather
    # than half-configured.
    twelve_labs_api_key: Optional[str] = None
    twelve_labs_index_id: Optional[str] = None
    twelve_labs_marengo_model_name: Optional[str] = None
    twelve_labs_marengo_model_version: Optional[str] = None
    twelve_labs_pegasus_model_name: Optional[str] = None
    twelve_labs_pegasus_model_version: Optional[str] = None
    twelve_labs_search_options: str = "visual,audio"
    twelve_labs_search_page_limit: int = Field(default=10, ge=1, le=50)
    twelve_labs_description_window_ms: int = Field(default=30000, gt=0)
    twelve_labs_max_description_windows: int = Field(default=120, gt=0)
    twelve_labs_pegasus_max_tokens: int = Field(default=512, gt=0)
    twelve_labs_reconcile_max_pages: int = Field(default=3, gt=0)
    twelve_labs_reconcile_page_limit: int = Field(default=50, ge=1, le=50)
    twelve_labs_reconcile_timeout_seconds: float = Field(default=10.0, gt=0)

    # Video evidence service: how far either side of a player timestamp still
    # counts as "here", and which provider's bindings it reads.
    video_provider: str = "twelvelabs"
    video_moment_before_ms: int = Field(default=15000, ge=0)
    video_moment_after_ms: int = Field(default=15000, ge=0)
    video_search_limit: int = Field(default=10, ge=1)
    video_search_timeout_seconds: float = Field(default=10.0, gt=0)

    # YouTube discovery (Tavily). Discovery stays unregistered without a key.
    tavily_api_key: Optional[str] = None
    tavily_search_depth: str = "basic"
    tavily_timeout_seconds: float = Field(default=10.0, gt=0)
    tavily_request_max_results: int = Field(default=10, ge=1, le=20)

    # YouTube media resolution (Tunelio, decision M3-YT-ANALYSIS). Off without
    # a key, with the documented risks: downloading breaks YouTube's Terms of
    # Service, the service can stop without notice, and the student's choice
    # of video reaches a third party. Each resolution spends credits (6 + 10),
    # so the ceilings below are refusals, not hints.
    tunelio_api_key: Optional[str] = None
    tunelio_quality: str = "360p"
    """A lecture is read for its slides, not its cinematography."""
    tunelio_timeout_seconds: float = Field(default=90.0, gt=0)
    tunelio_max_duration_seconds: int = Field(default=3600, gt=0)
    """TwelveLabs' own sync analysis stops at one hour."""
    tunelio_max_bytes: int = Field(default=500_000_000, gt=0)


__all__ = ["ContentSettings"]


def application_database_url() -> str:
    """The configured application database URL, or a clear configuration error.

    Scripts and the worker use M1's ``Settings.database_url``; there is no
    built-in default database, so an unset value can never silently target a
    local or shared instance.
    """

    from netra_api.config import Settings

    url = Settings().database_url
    if not url:
        raise RuntimeError("NETRA_DATABASE_URL is not set")
    return url
