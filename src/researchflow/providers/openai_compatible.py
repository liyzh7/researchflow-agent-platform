"""Disabled-by-default OpenAI-compatible chat-completions provider."""

import asyncio
import ipaddress
import json
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from researchflow.core.config import Settings
from researchflow.providers.errors import (
    LLMProviderAuthenticationError,
    LLMProviderInvalidRequestError,
    LLMProviderInvalidResponseError,
    LLMProviderRateLimitError,
    LLMProviderRedirectError,
    LLMProviderTimeoutError,
    LLMProviderUnavailableError,
    LLMProviderUpstreamError,
)
from researchflow.providers.llm import LLMRequest, LLMResponse

_MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Do not construct a second request, even for the same origin. This
        # protects every sensitive header, not only today's Authorization field.
        return None


class OpenAICompatibleLLMProvider:
    """Call a configured ``/v1/chat/completions`` endpoint without SDK lock-in.

    The provider never discovers credentials or endpoints. Remote hosts are
    rejected unless an operator explicitly enables them in validated settings.
    Redirects are always rejected, including same-origin and HTTPS downgrades.
    """

    name = "openai_compatible"

    def __init__(
        self,
        settings: Settings,
        *,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self._base_url = str(settings.llm_base_url).rstrip("/")
        self._model = settings.llm_model
        self._api_key = (
            settings.llm_api_key.get_secret_value() if settings.llm_api_key is not None else None
        )
        # Reject malformed credentials before urllib/http.client can include an
        # Authorization value in a header-validation or encoding exception.
        if self._api_key is not None and any(
            not 0x21 <= ord(character) <= 0x7E for character in self._api_key
        ):
            raise LLMProviderInvalidRequestError(
                "LLM API key contains unsupported header characters"
            )
        self._allow_remote = settings.llm_allow_remote
        # A private opener avoids the process-global urlopen redirect policy.
        # The injection seam is trusted test code, not an operator policy override.
        self._opener = opener if opener is not None else build_opener(_RejectRedirects()).open
        self._validate_endpoint()

    async def generate(self, request: LLMRequest, *, timeout_seconds: float) -> LLMResponse:
        if timeout_seconds <= 0:
            raise LLMProviderInvalidRequestError("timeout_seconds must be positive")
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._generate_sync, request, timeout_seconds),
                timeout=timeout_seconds,
            )
        except TimeoutError:
            raise LLMProviderTimeoutError from None

    def _generate_sync(self, llm_request: LLMRequest, timeout_seconds: float) -> LLMResponse:
        messages: list[dict[str, str]] = []
        if llm_request.system_prompt is not None:
            messages.append({"role": "system", "content": llm_request.system_prompt})
        messages.append({"role": "user", "content": llm_request.prompt})
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "stream": False,
            "temperature": 0,
        }
        if llm_request.response_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": llm_request.response_schema_name or "structured_response",
                    "strict": True,
                    "schema": llm_request.response_schema,
                },
            }

        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        outbound = Request(
            f"{self._base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with self._opener(outbound, timeout=timeout_seconds) as response:
                if 300 <= getattr(response, "status", 200) < 400:
                    raise LLMProviderRedirectError
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            # Error responses may contain secrets in their reason, URL, headers
            # or body. Close them unread and do not chain them into tracebacks.
            exc.close()
            if 300 <= exc.code < 400:
                raise LLMProviderRedirectError from None
            if exc.code in {401, 403}:
                raise LLMProviderAuthenticationError from None
            if exc.code == 429:
                raise LLMProviderRateLimitError from None
            if 500 <= exc.code:
                raise LLMProviderUpstreamError from None
            raise LLMProviderInvalidRequestError from None
        except TimeoutError:
            raise LLMProviderTimeoutError from None
        except URLError:
            raise LLMProviderUnavailableError from None

        if len(raw) > _MAX_RESPONSE_BYTES:
            raise LLMProviderInvalidResponseError("provider response exceeded size limit")
        try:
            document = json.loads(raw)
            choice = document["choices"][0]
            content = choice["message"]["content"]
            usage = document.get("usage") or {}
            if not isinstance(content, str) or not content.strip():
                raise ValueError("empty provider content")
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
            raise LLMProviderInvalidResponseError from None
        return LLMResponse(
            content=content,
            provider=self.name,
            model=self._model,
            is_mock=False,
            input_tokens=_optional_nonnegative_int(usage.get("prompt_tokens")),
            output_tokens=_optional_nonnegative_int(usage.get("completion_tokens")),
        )

    def _validate_endpoint(self) -> None:
        parsed = urlparse(self._base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise LLMProviderInvalidRequestError("LLM base URL must be HTTP(S)")
        if parsed.username is not None or parsed.password is not None:
            raise LLMProviderInvalidRequestError("credentials must not be embedded in the URL")
        if not self._allow_remote and not _is_loopback(parsed.hostname):
            raise LLMProviderInvalidRequestError("remote LLM endpoints are disabled")
        if not _is_loopback(parsed.hostname) and not self._api_key:
            raise LLMProviderAuthenticationError("remote LLM endpoint requires an API key")


def _is_loopback(hostname: str) -> bool:
    if hostname.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _optional_nonnegative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value
