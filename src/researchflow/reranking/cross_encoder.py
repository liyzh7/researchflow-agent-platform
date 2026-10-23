"""Lazy, bounded CPU CrossEncoder inference for a pinned local snapshot."""

import asyncio
import logging
import math
from pathlib import Path
from typing import Any

from opentelemetry import trace

from researchflow.core.config import Settings
from researchflow.core.resources import run_bounded_thread
from researchflow.observability.tracing import traced
from researchflow.providers.errors import (
    RerankerProviderError,
    RerankerProviderInvalidInputError,
    RerankerProviderInvalidResponseError,
    RerankerProviderTimeoutError,
    RerankerProviderUnavailableError,
)
from researchflow.reranking.types import (
    RerankerInfo,
    RerankRequest,
    RerankResponse,
    RerankScore,
)

logger = logging.getLogger("researchflow.reranker")


class LocalCrossEncoderReranker:
    name = "local_cross_encoder"

    def __init__(self, settings: Settings) -> None:
        self.info = RerankerInfo(
            provider=self.name,
            model_name=settings.reranker_model_name,
            revision=settings.reranker_model_revision,
            max_input_tokens=settings.reranker_max_input_tokens,
        )
        self.max_batch_size = settings.reranker_candidate_limit
        self._inference_batch_size = settings.reranker_batch_size
        self._cache_dir = settings.reranker_cache_dir
        self._local_files_only = settings.reranker_local_files_only
        self._cpu_threads = settings.reranker_cpu_threads
        self._model: Any | None = None
        self._load_lock = asyncio.Lock()
        self._inference_slots = asyncio.Semaphore(settings.reranker_max_concurrency)

    @traced("rerank.inference")
    async def score(self, request: RerankRequest, *, timeout_seconds: float) -> RerankResponse:
        if not request.candidates or len(request.candidates) > self.max_batch_size:
            raise RerankerProviderInvalidInputError("reranker batch size is outside limits")
        if not request.query.strip():
            raise RerankerProviderInvalidInputError("reranker query must not be blank")
        model = await self._ensure_model(timeout_seconds)
        try:
            scores = await run_bounded_thread(
                self._inference_slots, lambda: self._score_sync(model, request), timeout_seconds
            )
        except TimeoutError as exc:
            raise RerankerProviderTimeoutError from exc
        except RerankerProviderError:
            raise
        except Exception as exc:
            logger.exception("reranker.inference_failed item_count=%d", len(request.candidates))
            raise RerankerProviderUnavailableError("local reranker inference failed") from exc
        return RerankResponse(
            scores=tuple(
                RerankScore(segment_id=item.segment_id, score=score)
                for item, score in zip(request.candidates, scores, strict=True)
            ),
            model=self.info,
        )

    async def _ensure_model(self, timeout_seconds: float) -> Any:
        if self._model is not None:
            trace.get_current_span().set_attribute("model.warm_state", "WARM")
            return self._model
        async with self._load_lock:
            if self._model is not None:
                return self._model
            try:
                trace.get_current_span().set_attribute("model.warm_state", "COLD")
                model = await run_bounded_thread(
                    self._inference_slots, self._load_model_sync, timeout_seconds
                )
            except TimeoutError as exc:
                raise RerankerProviderTimeoutError from exc
            except RerankerProviderError:
                raise
            except Exception as exc:
                logger.exception(
                    "reranker.model_load_failed model=%s revision=%s",
                    self.info.model_name,
                    self.info.revision,
                )
                raise RerankerProviderUnavailableError("local reranker failed to load") from exc
            self._model = model
            logger.info(
                "reranker.model_loaded model=%s revision=%s",
                self.info.model_name,
                self.info.revision,
            )
            return model

    def _load_model_sync(self) -> Any:
        snapshot = self._snapshot_path()
        cache_kwargs: dict[str, dict[str, str]] = {}
        if snapshot.is_dir():
            source = str(snapshot)
            revision = None
        elif self._local_files_only:
            raise RerankerProviderUnavailableError("pinned reranker snapshot is not cached")
        else:
            source = self.info.model_name
            revision = self.info.revision
            cache_dir = str(self._cache_dir / "hub")
            cache_kwargs = {
                "model_kwargs": {"cache_dir": cache_dir},
                "processor_kwargs": {"cache_dir": cache_dir},
                "config_kwargs": {"cache_dir": cache_dir},
            }

        import torch
        from sentence_transformers import CrossEncoder

        torch.set_num_threads(self._cpu_threads)
        return CrossEncoder(
            source,
            device="cpu",
            revision=revision,
            local_files_only=self._local_files_only,
            trust_remote_code=False,
            max_length=self.info.max_input_tokens,
            **cache_kwargs,
        )

    def _score_sync(self, model: Any, request: RerankRequest) -> tuple[float, ...]:
        import torch

        pairs = [(request.query, item.content) for item in request.candidates]
        raw = model.predict(
            pairs,
            batch_size=min(len(pairs), self._inference_batch_size),
            show_progress_bar=False,
            activation_fn=torch.nn.Identity(),
            convert_to_numpy=True,
        )
        values = tuple(float(value) for value in raw.reshape(-1).tolist())
        if len(values) != len(pairs) or any(not math.isfinite(value) for value in values):
            raise RerankerProviderInvalidResponseError(
                "reranker output shape or values are invalid"
            )
        return values

    def _snapshot_path(self) -> Path:
        repository_dir = "models--" + self.info.model_name.replace("/", "--")
        return self._cache_dir / "hub" / repository_dir / "snapshots" / self.info.revision
