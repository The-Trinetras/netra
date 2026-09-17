# M2 backend/data implementation

PostgreSQL is the canonical store and uses the single `DATABASE_URL`
configuration variable with the `postgresql+asyncpg://` scheme. Alembic owns
schema changes; application startup does not mutate schema.

Reading blocks are stable navigable units. Search chunks are separate retrieval
records containing exact block IDs, source-version IDs, embedding version and
safe retrieval metadata. Pinecone is a rebuildable projection; every returned
chunk ID is resolved against PostgreSQL before evidence is returned.

Hybrid retrieval runs PostgreSQL FTS TOP 20 and Pinecone semantic TOP 20,
combines their ranked results with deterministic RRF using k=60, truncates to
TOP 12, optionally passes those candidates to the approved BGE reranker
boundary, and applies the PostgreSQL evidence gate to a final bounded set of
4-6 items (returning fewer when fewer valid items exist). PostgreSQL jobs
remain the durable at-least-once queue; leases, operation keys, retries and
outbox records are persisted in the same authoritative database. RAGAS is not
added to the runtime dependency set; `evaluation/scripts/m2_retrieval.py`
provides a dependency-free dataset/case shape for a separately managed
evaluation environment.

PostgreSQL is authoritative for evidence text and access decisions. Pinecone
is rebuildable; its vector ID is always `search_chunks.chunk_id`, and its
metadata is candidate-filtering context rather than authoritative evidence
text. The local reranker uses `FlagEmbedding==1.4.2` for the approved
`BAAI/bge-reranker-v2-m3` cross-encoder. It loads lazily once per worker
process, defaults to CPU, and accepts no more than the frozen RRF Top-12
candidate set. Model weights are runtime artifacts and are not stored in Git.
Inference is bounded and runs off the async event loop. Explicit provider
unavailability falls back to deterministic pre-rerank ordering; malformed
model output and unexpected runtime errors remain visible. Canonical passage
text is resolved and authorized from PostgreSQL before it reaches BGE, and
evidence is resolved again after reranking before model context is built.

`netra_api.content.retrieval.factory.build_postgres_retrieval_service` is the
session-scoped application composition boundary. It reuses the caller-owned
SQLAlchemy `AsyncSession` to construct PostgreSQL FTS, Pinecone semantic
search, canonical evidence resolution, and the optional lazy BGE adapter. It
does not create a second engine or session factory. The API entrypoint owns
engine lifecycle and health/metrics routes; a public retrieval transport route
remains an M1/M2 integration boundary.

## Stage C PDF ingestion

`PyMuPDF==1.28.2` is the normal local PDF parser. The adapter processes pages
sequentially and emits page-aware parser blocks with deterministic document
metadata, bounding boxes, ordering, and stable Section/Subsection parent IDs
when font/layout evidence supports that distinction. The existing chunker
consumes those blocks through the existing ReadingBlock-to-search-chunk
mapping; Reading Blocks remain separate navigation units.

The current parser contract does not provide reliable printed-page labels,
semantic equation recognition, image understanding, or general table
reconstruction. Images are not sent to a model. An image-only/scanned
document reports `ocr_required`; the ingestion worker may then use the bounded
configured Tesseract adapter without fabricating text. LlamaParse remains a
fallback boundary for complex documents, and M3 owns multimedia understanding.

## Gemini embedding projection boundary

PostgreSQL canonical search chunks are passed to the configured Gemini
embedding adapter. The adapter returns vectors together with an exact
embedding specification (`model`, `dimension`, and deterministic `version`).
The Pinecone projection worker persists those vectors as rebuildable derived
data; neither embeddings nor Pinecone replace PostgreSQL authority.
The model and dimension remain pinned for an index. Changing either requires
a controlled re-embedding and re-projection process rather than mixing vector
configurations in one index.
