"""TEST-ONLY doubles for multimedia provider adapters.

Nothing here is a provider response captured from a live account. Every
value is synthetic and shaped after the pinned SDK READMEs; the adapters'
real SDK surface stays unverified until an authorized install (M3-SDK-1/2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from netra_api.multimedia.providers.twelve_labs_client import (
    IndexedAssetPage,
    IndexedAssetState,
    RawAnalysis,
    RawSearchHit,
    TwelveLabsSettings,
)
from netra_api.platform.tracing import ExportSettings, InMemorySpanExporter, build_tracer

FAST_EXPORT = ExportSettings(schedule_delay_seconds=0.01, export_timeout_seconds=1.0, max_retries=0, retry_backoff_seconds=0.01)


def local_tracer():
    exporter = InMemorySpanExporter()
    return build_tracer("local", exporter=exporter, settings=FAST_EXPORT, service_name="netra-worker"), exporter


def settings(**overrides: Any) -> TwelveLabsSettings:
    values: Dict[str, Any] = dict(
        index_id="idx-test",
        marengo_model_name="marengo-test-model",
        marengo_model_version="test-1",
        pegasus_model_name="pegasus-test-model",
        pegasus_model_version="test-1",
        search_options=("visual", "audio"),
        search_page_limit=10,
        description_window_ms=30_000,
        max_description_windows=10,
        pegasus_max_tokens=512,
        reconcile_max_pages=3,
        reconcile_page_limit=2,
        reconcile_timeout_seconds=5.0,
    )
    values.update(overrides)
    return TwelveLabsSettings(**values)


class Flag:
    def __init__(self, cancelled: bool = False) -> None:
        self.cancelled = cancelled

    def is_cancelled(self) -> bool:
        return self.cancelled

    def reason(self) -> str:
        return "test cancellation"


@dataclass
class FakeGateway:
    """Synchronous, like the real SDK. Records every call."""

    state: IndexedAssetState = field(
        default_factory=lambda: IndexedAssetState(indexed_asset_id="ia-1", status="ready", duration_ms=90_000, asset_id="as-1")
    )
    pages: List[IndexedAssetPage] = field(default_factory=list)
    hits: List[RawSearchHit] = field(default_factory=list)
    analyses: Dict[int, RawAnalysis] = field(default_factory=dict)
    default_analysis: RawAnalysis = field(default_factory=lambda: RawAnalysis(text="Slide: graph of Voltage (V) against Current (A).", finish_reason="stop"))
    calls: List[tuple] = field(default_factory=list)
    fail_with: Dict[str, BaseException] = field(default_factory=dict)
    on_call: Optional[Any] = None

    def _record(self, name: str, **kwargs: Any) -> None:
        self.calls.append((name, kwargs))
        if self.on_call is not None:
            self.on_call(name)
        if name in self.fail_with:
            raise self.fail_with[name]

    def create_asset_from_url(self, *, url: str, netra_ref: str) -> str:
        self._record("create_asset", url=url, netra_ref=netra_ref)
        return "as-1"

    def index_asset(self, *, index_id: str, asset_id: str, netra_ref: str) -> str:
        self._record("index_asset", index_id=index_id, asset_id=asset_id, netra_ref=netra_ref)
        return "ia-1"

    def indexed_asset(self, *, index_id: str, indexed_asset_id: str) -> IndexedAssetState:
        self._record("indexed_asset", index_id=index_id, indexed_asset_id=indexed_asset_id)
        return self.state

    def list_indexed_assets(self, *, index_id: str, page: int, page_limit: int) -> IndexedAssetPage:
        self._record("list", index_id=index_id, page=page, page_limit=page_limit)
        return self.pages[page - 1] if page - 1 < len(self.pages) else IndexedAssetPage(items=(), has_more=False)

    def search(self, *, index_id: str, query_text: str, search_options, page_limit: int) -> List[RawSearchHit]:
        self._record("search", index_id=index_id, query_text=query_text, search_options=tuple(search_options), page_limit=page_limit)
        return list(self.hits)

    def analyze(self, *, model_name: str, asset_id: str, prompt: str, start_seconds: float, end_seconds: float, max_tokens: int) -> RawAnalysis:
        self._record("analyze", model_name=model_name, asset_id=asset_id, start_seconds=start_seconds, end_seconds=end_seconds, max_tokens=max_tokens)
        return self.analyses.get(int(start_seconds * 1000), self.default_analysis)


class StatusError(Exception):
    """Shaped like a Fern-generated SDK ApiError: a status_code attribute."""

    def __init__(self, status_code: int, message: str = "provider said: account acct_123 url=https://x/?sig=secret") -> None:
        self.status_code = status_code
        super().__init__(message)


class InvalidAPIKeyError(Exception):
    """Same class name as tavily-python's failure; message must never be echoed."""


class ReadTimeout(Exception):
    """Same class name as httpx.ReadTimeout."""


@dataclass
class FakeMediaUrls:
    url: Optional[str] = "https://storage.invalid/private/key?X-Signature=secret"
    calls: List[tuple] = field(default_factory=list)

    async def fetchable_url(self, *, external_ref: str, content_type: str) -> Optional[str]:
        self.calls.append((external_ref, content_type))
        return self.url
