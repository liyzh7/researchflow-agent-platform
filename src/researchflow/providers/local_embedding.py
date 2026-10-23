"""Bounded, lazy-loaded CPU inference for the pinned multilingual E5 model."""

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from opentelemetry import trace

from researchflow.core.config import Settings
from researchflow.core.resources import run_bounded_thread
from researchflow.observability.tracing import traced
from researchflow.providers.embedding import (
    DocumentTokenization,
    EmbeddingModelInfo,
    EmbeddingPurpose,
    EmbeddingRequest,
    EmbeddingResponse,
    validate_embedding_vectors,
)
from researchflow.providers.errors import (
    EmbeddingProviderError,
    EmbeddingProviderInvalidInputError,
    EmbeddingProviderInvalidVectorError,
    EmbeddingProviderTimeoutError,
    EmbeddingProviderUnavailableError,
)

logger = logging.getLogger("researchflow.embedding")
T = TypeVar("T")


class LocalEmbeddingProvider:
    """Load one local model instance and run CPU work outside the event loop."""

    name = "local"

    def __init__(self, settings: Settings) -> None:
        self.info = EmbeddingModelInfo(
            provider=self.name,
            model_name=settings.embedding_model_name,
            revision=settings.embedding_model_revision,
            dimension=settings.embedding_dimension,
            normalized=settings.embedding_normalize,
            query_prefix=settings.embedding_query_prefix,
            passage_prefix=settings.embedding_passage_prefix,
            max_input_tokens=settings.embedding_max_input_tokens,
        )
        self.max_batch_size = settings.embedding_batch_size
        self._cache_dir = settings.embedding_cache_dir
        self._local_files_only = settings.embedding_local_files_only
        self._cpu_threads = settings.embedding_cpu_threads
        self._model: Any | None = None
        self._load_lock = asyncio.Lock()
        self._inference_slots = asyncio.Semaphore(settings.embedding_max_concurrency)

    async def embed_queries(
        self, request: EmbeddingRequest, *, timeout_seconds: float
    ) -> EmbeddingResponse:
        return await self._embed(request, purpose="query", timeout_seconds=timeout_seconds)

    async def embed_documents(
        self, request: EmbeddingRequest, *, timeout_seconds: float
    ) -> EmbeddingResponse:
        return await self._embed(request, purpose="document", timeout_seconds=timeout_seconds)

    @traced("embedding.tokenize")
    async def tokenize_document(self, text: str, *, timeout_seconds: float) -> DocumentTokenization:
        if not text:
            raise EmbeddingProviderInvalidInputError("document text must not be empty")
        model = await self._ensure_model(timeout_seconds)
        return await self._run_bounded(
            lambda: self._tokenize_document_sync(model, text), timeout_seconds
        )

    @traced("embedding.count_tokens")
    async def count_document_tokens(
        self, texts: tuple[str, ...], *, timeout_seconds: float
    ) -> tuple[int, ...]:
        self._validate_text_batch(texts)
        model = await self._ensure_model(timeout_seconds)
        return await self._run_bounded(
            lambda: self._count_tokens_sync(model, texts, "document"), timeout_seconds
        )

    @traced("embedding.inference")
    async def _embed(
        self,
        request: EmbeddingRequest,
        *,
        purpose: EmbeddingPurpose,
        timeout_seconds: float,
    ) -> EmbeddingResponse:
        texts = request.texts
        self._validate_text_batch(texts)
        model = await self._ensure_model(timeout_seconds)
        try:
            vectors = await self._run_bounded(
                lambda: self._encode_sync(model, texts, purpose), timeout_seconds
            )
        except EmbeddingProviderError:
            raise
        except Exception as exc:
            logger.exception(
                "embedding.inference_failed purpose=%s item_count=%d", purpose, len(texts)
            )
            raise EmbeddingProviderUnavailableError("local embedding inference failed") from exc
        return EmbeddingResponse(vectors=vectors, model=self.info, purpose=purpose)

    async def _ensure_model(self, timeout_seconds: float) -> Any:
        if self._model is not None:
            trace.get_current_span().set_attribute("model.warm_state", "WARM")
            return self._model
        async with self._load_lock:
            if self._model is not None:
                return self._model
            try:
                trace.get_current_span().set_attribute("model.warm_state", "COLD")
                model = await self._run_bounded(self._load_model_sync, timeout_seconds)
            except EmbeddingProviderError:
                raise
            except Exception as exc:
                logger.exception(
                    "embedding.model_load_failed model=%s revision=%s",
                    self.info.model_name,
                    self.info.revision,
                )
                raise EmbeddingProviderUnavailableError(
                    "local embedding model failed to load"
                ) from exc
            self._model = model
            logger.info(
                "embedding.model_loaded model=%s revision=%s dimension=%d normalized=%s",
                self.info.model_name,
                self.info.revision,
                self.info.dimension,
                self.info.normalized,
            )
            return model

    def _load_model_sync(self) -> Any:
        snapshot = self._snapshot_path()
        if snapshot.is_dir():
            model_source = str(snapshot)
        elif self._local_files_only:
            raise EmbeddingProviderUnavailableError("pinned model snapshot is not cached")
        else:
            model_source = self.info.model_name

        import torch
        from sentence_transformers import SentenceTransformer

        torch.set_num_threads(self._cpu_threads)

        model = SentenceTransformer(
            model_source,
            device="cpu",
            cache_folder=str(self._cache_dir / "hub"),
            revision=None if snapshot.is_dir() else self.info.revision,
            local_files_only=self._local_files_only,
            trust_remote_code=False,
        )
        dimension = model.get_embedding_dimension()
        if dimension != self.info.dimension:
            raise EmbeddingProviderInvalidVectorError(
                f"configured dimension {self.info.dimension} does not match model dimension"
            )
        if model.max_seq_length != self.info.max_input_tokens:
            raise EmbeddingProviderInvalidInputError(
                "configured token limit does not match the pinned model"
            )
        if not getattr(model.tokenizer, "is_fast", False):
            raise EmbeddingProviderUnavailableError("model tokenizer lacks exact offset mapping")
        return model

    def _snapshot_path(self) -> Path:
        repository_dir = "models--" + self.info.model_name.replace("/", "--")
        return self._cache_dir / "hub" / repository_dir / "snapshots" / self.info.revision

    def _encode_sync(
        self,
        model: Any,
        texts: tuple[str, ...],
        purpose: EmbeddingPurpose,
    ) -> tuple[tuple[float, ...], ...]:
        prefixed = tuple(self._add_prefix(text, purpose) for text in texts)
        counts = self._token_counts(model, prefixed)
        if any(count > self.info.max_input_tokens for count in counts):
            raise EmbeddingProviderInvalidInputError(
                "embedding input exceeds the model token limit; segment it first"
            )
        vectors = model.encode(
            list(prefixed),
            batch_size=min(len(prefixed), self.max_batch_size),
            normalize_embeddings=self.info.normalized,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return self._validate_vectors(vectors, expected_count=len(prefixed))

    def _tokenize_document_sync(self, model: Any, text: str) -> DocumentTokenization:
        raw = model.tokenizer(
            text,
            add_special_tokens=False,
            return_offsets_mapping=True,
            truncation=False,
        )
        offsets = tuple(
            (int(start), int(end)) for start, end in raw["offset_mapping"] if int(end) > int(start)
        )
        prefixed_count = self._token_counts(model, (self._add_prefix(text, "document"),))[0]
        return DocumentTokenization(
            offsets=offsets,
            prefixed_token_count=prefixed_count,
            max_input_tokens=self.info.max_input_tokens,
        )

    def _count_tokens_sync(
        self, model: Any, texts: tuple[str, ...], purpose: EmbeddingPurpose
    ) -> tuple[int, ...]:
        prefixed = tuple(self._add_prefix(text, purpose) for text in texts)
        return self._token_counts(model, prefixed)

    @staticmethod
    def _token_counts(model: Any, prefixed: tuple[str, ...]) -> tuple[int, ...]:
        encoded = model.tokenizer(
            list(prefixed),
            add_special_tokens=True,
            padding=False,
            truncation=False,
        )
        return tuple(len(input_ids) for input_ids in encoded["input_ids"])

    def _add_prefix(self, text: str, purpose: EmbeddingPurpose) -> str:
        prefix = self.info.query_prefix if purpose == "query" else self.info.passage_prefix
        other = self.info.passage_prefix if purpose == "query" else self.info.query_prefix
        if text.startswith(prefix):
            return text
        if text.startswith(other):
            raise EmbeddingProviderInvalidInputError("input has the wrong E5 prefix")
        return prefix + text

    def _validate_text_batch(self, texts: tuple[str, ...]) -> None:
        if not texts or len(texts) > self.max_batch_size:
            raise EmbeddingProviderInvalidInputError(
                "embedding batch size is outside configured limits"
            )
        if any(not text or not text.strip() for text in texts):
            raise EmbeddingProviderInvalidInputError("embedding text must not be blank")

    def _validate_vectors(
        self, vectors: Any, *, expected_count: int
    ) -> tuple[tuple[float, ...], ...]:
        if tuple(vectors.shape) != (expected_count, self.info.dimension):
            raise EmbeddingProviderInvalidVectorError("embedding output shape is invalid")
        return validate_embedding_vectors(
            vectors.tolist(), self.info, expected_count=expected_count
        )

    async def _run_bounded(self, operation: Callable[[], T], timeout_seconds: float) -> T:
        try:
            return await run_bounded_thread(self._inference_slots, operation, timeout_seconds)
        except TimeoutError as exc:
            raise EmbeddingProviderTimeoutError from exc
