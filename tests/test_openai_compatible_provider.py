"""Provider contracts with newly authored, disposable synthetic payloads.

These fixtures were written for this standalone candidate. No evaluation corpus,
recorded model response, personal document or working credential is used.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError

import pytest

from researchflow.core.config import Settings
from researchflow.providers.errors import (
    LLMProviderAuthenticationError,
    LLMProviderInvalidRequestError,
    LLMProviderInvalidResponseError,
    LLMProviderRateLimitError,
    LLMProviderTimeoutError,
    LLMProviderUpstreamError,
)
from researchflow.providers.llm import LLMRequest
from researchflow.providers.openai_compatible import OpenAICompatibleLLMProvider


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class SyntheticResponse:
    def __init__(self, document: object) -> None:
        self.raw = json.dumps(document).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _size: int) -> bytes:
        return self.raw


def provider_settings(**changes) -> Settings:
    return Settings(
        environment="test",
        database_password="candidate-provider-db-placeholder-unused",
        llm_provider_mode="openai_compatible",
        _env_file=None,
        **changes,
    )


@pytest.mark.anyio
async def test_structured_response_and_usage_contract() -> None:
    observed = {}

    def opener(request, *, timeout):
        observed["payload"] = json.loads(request.data)
        observed["timeout"] = timeout
        return SyntheticResponse(
            {
                "choices": [{"message": {"content": '{"code":"CANDIDATE_A17"}'}}],
                "usage": {"prompt_tokens": 9, "completion_tokens": 3},
            }
        )

    result = await OpenAICompatibleLLMProvider(provider_settings(), opener=opener).generate(
        LLMRequest(
            prompt="Return a code for an imaginary storage bin.",
            system_prompt="Synthetic transport fixture; no model is contacted.",
            response_schema_name="bin_code",
            response_schema={"type": "object", "properties": {"code": {"type": "string"}}},
        ),
        timeout_seconds=1,
    )
    assert result.content == '{"code":"CANDIDATE_A17"}'
    assert (result.input_tokens, result.output_tokens) == (9, 3)
    assert observed["timeout"] == 1
    assert observed["payload"]["response_format"]["type"] == "json_schema"
    assert observed["payload"]["response_format"]["json_schema"]["name"] == "bin_code"


def test_remote_endpoint_is_disabled_without_explicit_opt_in() -> None:
    with pytest.raises(LLMProviderInvalidRequestError, match="disabled"):
        OpenAICompatibleLLMProvider(provider_settings(llm_base_url="https://inference.example/v1"))


def test_remote_opt_in_does_not_remove_authentication_requirement() -> None:
    with pytest.raises(LLMProviderAuthenticationError):
        OpenAICompatibleLLMProvider(
            provider_settings(llm_base_url="https://inference.example/v1", llm_allow_remote=True)
        )


@pytest.mark.anyio
async def test_timeout_classification() -> None:
    def opener(*_args, **_kwargs):
        raise TimeoutError

    provider = OpenAICompatibleLLMProvider(provider_settings(), opener=opener)
    with pytest.raises(LLMProviderTimeoutError):
        await provider.generate(LLMRequest(prompt="Synthetic timeout contract."), timeout_seconds=1)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (401, LLMProviderAuthenticationError),
        (429, LLMProviderRateLimitError),
        (503, LLMProviderUpstreamError),
    ],
)
async def test_http_error_classification(status: int, error_type: type[Exception]) -> None:
    def opener(*_args, **_kwargs):
        raise HTTPError("http://127.0.0.1", status, "synthetic upstream status", None, None)

    provider = OpenAICompatibleLLMProvider(provider_settings(), opener=opener)
    with pytest.raises(error_type):
        await provider.generate(LLMRequest(prompt="Synthetic status contract."), timeout_seconds=1)


@pytest.mark.anyio
async def test_non_json_payload_is_rejected() -> None:
    response = SyntheticResponse(None)
    response.raw = b"candidate-payload-is-deliberately-not-json"
    provider = OpenAICompatibleLLMProvider(provider_settings(), opener=lambda *_a, **_k: response)
    with pytest.raises(LLMProviderInvalidResponseError):
        await provider.generate(LLMRequest(prompt="Synthetic parsing contract."), timeout_seconds=1)


@pytest.mark.anyio
async def test_live_loopback_transport_contract(monkeypatch) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            document = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert document["messages"][-1]["content"] == "Read the imaginary bin label."
            raw = json.dumps({"choices": [{"message": {"content": "BIN_CANDIDATE_42"}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, _format, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    try:
        settings = provider_settings(llm_base_url=f"http://127.0.0.1:{server.server_port}/v1")
        result = await OpenAICompatibleLLMProvider(settings).generate(
            LLMRequest(prompt="Read the imaginary bin label."), timeout_seconds=2
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert not thread.is_alive()
    assert result.content == "BIN_CANDIDATE_42"
