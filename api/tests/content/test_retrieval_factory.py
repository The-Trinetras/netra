from netra_api.content.settings import ContentSettings
from netra_api.content.providers.pinecone import PineconeVectorIndex
from netra_api.content.retrieval.factory import (build_hybrid_retrieval_service,
                                                  build_postgres_retrieval_service)
from netra_api.content.retrieval.exact_search import PostgresExactSearch
from netra_api.content.retrieval.postgres_evidence import AsyncPostgresEvidenceResolver
from netra_api.content.retrieval.reranker import BGEReranker
from netra_api.content.retrieval.semantic_search import PineconeSemanticSearch


def test_retrieval_factory_keeps_reranker_disabled_by_default():
    service = build_hybrid_retrieval_service(object(), object(), object(), ContentSettings())
    assert service.reranker is None


def test_retrieval_factory_constructs_one_lazy_bge_adapter_when_enabled():
    settings = ContentSettings(
        reranker_enabled=True,
        reranker_model_id="BAAI/bge-reranker-v2-m3",
        reranker_batch_size=3,
        reranker_device="cpu",
        reranker_max_concurrency=2,
    )
    service = build_hybrid_retrieval_service(object(), object(), object(), settings)
    assert isinstance(service.reranker, BGEReranker)
    assert service.reranker.settings is settings
    assert service.reranker.settings.reranker_model_id == "BAAI/bge-reranker-v2-m3"
    assert service.reranker.settings.reranker_batch_size == 3
    assert service.reranker.settings.reranker_device == "cpu"
    assert service.reranker.settings.reranker_max_concurrency == 2
    assert service.reranker._model is None


def test_postgres_factory_reuses_one_session_and_constructs_real_graph():
    session = object()
    service = build_postgres_retrieval_service(session, ContentSettings())
    assert isinstance(service.lexical, PostgresExactSearch)
    assert service.lexical.session is session
    assert isinstance(service.semantic, PineconeSemanticSearch)
    assert isinstance(service.semantic.index, PineconeVectorIndex)
    assert isinstance(service.evidence_resolver, AsyncPostgresEvidenceResolver)
    assert service.evidence_resolver.session is session
    assert service.reranker is None


def test_postgres_factory_enabled_bge_remains_lazy():
    service = build_postgres_retrieval_service(object(), ContentSettings(reranker_enabled=True))
    assert isinstance(service.reranker, BGEReranker)
    assert service.reranker._model is None
