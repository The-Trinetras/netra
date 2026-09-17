from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    """Runtime configuration shared by API persistence and adapters."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "postgresql+asyncpg://netra:netra@localhost:5432/netra"
    database_pool_size: int = 5
    embedding_model: str = "gemini-embedding-001"
    embedding_dimension: int = 1536
    gemini_api_key: str | None = None
    gemini_embedding_model: str = "gemini-embedding-001"
    gemini_embedding_dimension: int = 1536
    gemini_embedding_batch_size: int = 100
    pinecone_api_key: str | None = None
    pinecone_index_name: str | None = None
    pinecone_namespace: str = "netra"
    pinecone_upsert_batch_size: int = 100
    parse_workers: int = 3
    block_workers: int = 3
    embed_workers: int = 2
    projection_workers: int = 3
    activation_workers: int = 1
    worker_lease_duration_seconds: int = 60
    worker_poll_interval_seconds: float = 1.0
    outbox_lease_duration_seconds: int = 60
    s3_bucket: str | None = None
    aws_region: str | None = None
    storage_provider: str = "s3"
    local_fixture_root: str | None = None
    chunk_target_tokens: int = 500
    chunk_min_tokens: int = 350
    chunk_max_tokens: int = 700
    chunk_overlap_tokens: int = 64
    vector_top_k: int = 20
    fts_top_k: int = 20
    rrf_k: int = 60
    fusion_top_k: int = 12
    rerank_top_k: int = 12
    reranker_model_id: str = "BAAI/bge-reranker-v2-m3"
    reranker_enabled: bool = False
    reranker_batch_size: int = Field(default=4, ge=1, le=12)
    reranker_device: str = "cpu"
    reranker_max_concurrency: int = Field(default=1, ge=1, le=4)
    final_evidence_min: int = 4
    final_evidence_max: int = 6
    tesseract_executable: str = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    ocr_language: str = "eng"
    ocr_timeout_seconds: float = 30.0
    ocr_dpi: int = 300
    observability_enabled: bool = True
    log_format: str = "json"
    tracing_enabled: bool = True
    metrics_enabled: bool = True
