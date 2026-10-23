"""Candidate-authored security fixtures; sockets stay on disposable loopback ports.

All prompts, response strings and credential markers are new synthetic test data.
The downgrade tests emulate the first HTTPS response and do not claim TLS testing.
"""

import io
import json
import logging
import threading
import traceback
from contextlib import ExitStack, contextmanager
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener
from urllib.response import addinfourl

import pytest

from researchflow.observability.tracing import Telemetry, active_telemetry, operation
from researchflow.providers.errors import (
    LLMProviderError,
    LLMProviderInvalidRequestError,
    LLMProviderRedirectError,
)
from researchflow.providers.llm import LLMRequest
from researchflow.providers.openai_compatible import OpenAICompatibleLLMProvider, _RejectRedirects


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


_KEY = "candidate-fixture-bearer-no-account-associated"
_COOKIE = "candidate-fixture-cookie-no-session-associated"
_HEADER = "candidate-fixture-header-no-service-associated"
_LOCATION = "candidate-fixture-location-marker-924"
_MARKERS = (_KEY, _COOKIE, _HEADER, _LOCATION)


def _settings(**changes):
    from researchflow.core.config import Settings

    return Settings(
        environment="test",
        database_password="candidate-security-db-placeholder-unused",
        llm_provider_mode="openai_compatible",
        _env_file=None,
        **changes,
    )


@contextmanager
def _endpoint():
    state = {"status": 200, "location": None, "requests": []}

    class Handler(BaseHTTPRequestHandler):
        def handle_request(self):
            length = int(self.headers.get("Content-Length", "0"))
            if length:
                self.rfile.read(length)
            state["requests"].append(
                {
                    "path": self.path,
                    "method": self.command,
                    "sensitive": any(
                        self.headers.get(name) for name in ("Authorization", "Cookie", "X-API-Key")
                    ),
                }
            )
            raw = json.dumps(
                {"choices": [{"message": {"content": "CANDIDATE_LOOPBACK_924"}}]}
            ).encode()
            self.send_response(state["status"])
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            if state["location"]:
                self.send_header("Location", state["location"])
            self.end_headers()
            if state["status"] != 304:
                try:
                    self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        do_POST = do_GET = handle_request

        def log_message(self, _format, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    state["url"] = f"http://127.0.0.1:{server.server_port}"
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


@pytest.fixture
def endpoints(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    with ExitStack() as stack:
        yield stack.enter_context(_endpoint()), stack.enter_context(_endpoint())


@pytest.mark.anyio
async def test_normal_authenticated_request_reaches_only_initial_endpoint(endpoints):
    source, sink = endpoints
    provider = OpenAICompatibleLLMProvider(
        _settings(llm_base_url=source["url"] + "/v1", llm_api_key=_KEY)
    )
    result = await provider.generate(
        LLMRequest(prompt="Read imaginary bin 924."), timeout_seconds=2
    )
    assert result.content == "CANDIDATE_LOOPBACK_924"
    assert result.is_mock is False
    assert len(source["requests"]) == 1
    assert source["requests"][0]["sensitive"] is True
    assert sink["requests"] == []


@pytest.mark.anyio
@pytest.mark.parametrize("status", [300, 301, 302, 303, 304, 305, 307, 308, 399])
@pytest.mark.parametrize("destination", ["same_origin", "different_port", "different_hostname"])
async def test_all_3xx_stop_before_second_request(endpoints, status, destination):
    source, sink = endpoints
    target = source["url"] if destination == "same_origin" else sink["url"]
    if destination == "different_hostname":
        target = target.replace("127.0.0.1", "localhost")
    source.update(status=status, location=target + "/never-request-this?marker=" + _LOCATION)
    provider = OpenAICompatibleLLMProvider(
        _settings(
            llm_base_url=source["url"] + "/v1",
            llm_api_key=_KEY,
            llm_allow_remote=destination == "different_hostname",
        )
    )
    with pytest.raises(LLMProviderRedirectError, match="redirect blocked") as error:
        await provider.generate(
            LLMRequest(prompt="Synthetic redirect boundary."), timeout_seconds=2
        )
    assert len(source["requests"]) == 1
    assert source["requests"][0]["path"] == "/v1/chat/completions"
    assert sink["requests"] == []
    assert not any(item["path"].startswith("/never-request-this") for item in source["requests"])
    assert all(
        marker not in "".join(traceback.format_exception(error.value)) for marker in _MARKERS
    )


@pytest.mark.anyio
@pytest.mark.parametrize("shape", ["self", "two_endpoints"])
async def test_loop_stops_after_first_response(endpoints, shape):
    source, sink = endpoints
    source.update(status=302, location=source["url"] if shape == "self" else sink["url"])
    sink.update(status=302, location=source["url"])
    provider = OpenAICompatibleLLMProvider(_settings(llm_base_url=source["url"] + "/v1"))
    with pytest.raises(LLMProviderRedirectError):
        await provider.generate(LLMRequest(prompt="Synthetic loop boundary."), timeout_seconds=2)
    assert len(source["requests"]) == 1
    assert sink["requests"] == []


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_get_redirect_cannot_forward_any_sensitive_header(endpoints, status):
    source, sink = endpoints
    source.update(status=status, location=sink["url"] + "/never-request-this")
    provider = OpenAICompatibleLLMProvider(_settings())
    request = Request(
        source["url"],
        headers={
            "Authorization": "Bearer " + _KEY,
            "Cookie": "session=" + _COOKIE,
            "X-API-Key": _HEADER,
        },
    )
    with pytest.raises(HTTPError) as error:
        provider._opener(request, timeout=2)
    error.value.close()
    assert len(source["requests"]) == 1
    assert sink["requests"] == []


@pytest.mark.anyio
@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
async def test_emulated_https_cannot_redirect_to_live_http_sink(endpoints, status):
    _source, sink = endpoints
    observed = []

    class EmulatedHTTPS(HTTPSHandler):
        def https_open(self, request):
            observed.append(request.full_url)
            headers = Message()
            headers["Location"] = sink["url"] + "/never-request-this"
            response = addinfourl(io.BytesIO(b""), headers, request.full_url, status)
            response.msg = "Candidate emulated redirect"
            return response

    opener = build_opener(ProxyHandler({}), _RejectRedirects(), EmulatedHTTPS())
    provider = OpenAICompatibleLLMProvider(
        _settings(llm_base_url="https://127.0.0.1/v1", llm_api_key=_KEY), opener=opener.open
    )
    with pytest.raises(LLMProviderRedirectError):
        await provider.generate(
            LLMRequest(prompt="Synthetic downgrade boundary."), timeout_seconds=2
        )
    assert observed == ["https://127.0.0.1/v1/chat/completions"]
    assert sink["requests"] == []


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["redirect", "authentication", "unavailable", "timeout"])
async def test_error_traceback_logs_and_spans_hide_transport_markers(caplog, failure):
    body = io.BytesIO(_KEY.encode())

    def opener(*_args, **_kwargs):
        if failure == "unavailable":
            raise URLError(_KEY)
        if failure == "timeout":
            raise TimeoutError(_KEY)
        headers = Message()
        headers["Location"] = "http://127.0.0.1/?marker=" + _LOCATION
        headers["Cookie"] = _COOKIE
        headers["X-API-Key"] = _HEADER
        raise HTTPError(
            "http://127.0.0.1/?marker=" + _LOCATION,
            302 if failure == "redirect" else 401,
            _KEY,
            headers,
            body,
        )

    telemetry = Telemetry()
    token = active_telemetry.set(telemetry)
    try:
        provider = OpenAICompatibleLLMProvider(_settings(llm_api_key=_KEY), opener=opener)
        with caplog.at_level(logging.ERROR), pytest.raises(LLMProviderError) as error:
            try:
                with operation("candidate.provider.boundary"):
                    await provider.generate(
                        LLMRequest(prompt="Synthetic privacy boundary."), timeout_seconds=2
                    )
            except LLMProviderError:
                logging.getLogger("candidate.provider_test").exception("candidate.request_failed")
                raise
        spans = telemetry.exporter.get_finished_spans()
        assert len(spans) == 1
        surfaces = "\n".join(
            [
                str(error.value),
                repr(error.value),
                caplog.text,
                "".join(traceback.format_exception(error.value)),
                spans[0].to_json(),
            ]
        )
        assert all(marker not in surfaces for marker in _MARKERS)
        assert all(header not in caplog.text for header in ("Authorization", "Cookie", "X-API-Key"))
        assert all(event.name != "exception" for event in spans[0].events)
        if failure in {"redirect", "authentication"}:
            assert body.closed
    finally:
        active_telemetry.reset(token)
        telemetry.close()


def test_url_credentials_are_rejected_without_secret_echo():
    with pytest.raises(LLMProviderError) as error:
        OpenAICompatibleLLMProvider(
            _settings(
                llm_base_url=f"http://candidate-user:{_KEY}@127.0.0.1/v1", llm_api_key=_HEADER
            )
        )
    assert all(
        marker not in "".join(traceback.format_exception(error.value)) for marker in _MARKERS
    )


def test_injected_redirect_closes_before_reading_untrusted_body():
    class RedirectResponse:
        status = 302
        closed = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.closed = True

        def read(self, _size):
            pytest.fail("candidate redirect fixture must stay unread")

    response = RedirectResponse()
    provider = OpenAICompatibleLLMProvider(_settings(), opener=lambda *_a, **_k: response)
    with pytest.raises(LLMProviderRedirectError):
        provider._generate_sync(LLMRequest(prompt="Synthetic body handling boundary."), 2)
    assert response.closed


@pytest.mark.anyio
@pytest.mark.parametrize("invalid", ["\r\n", "\r\n ", "\t", "\x00", "\x7f", "\u2603"])
async def test_invalid_header_configuration_is_sanitized_before_transport(endpoints, invalid):
    source, sink = endpoints
    with pytest.raises(LLMProviderInvalidRequestError) as error:
        provider = OpenAICompatibleLLMProvider(
            _settings(llm_base_url=source["url"] + "/v1", llm_api_key=_KEY + invalid + _HEADER)
        )
        await provider.generate(
            LLMRequest(prompt="Synthetic invalid header boundary."), timeout_seconds=2
        )
    surface = "".join(traceback.format_exception(error.value))
    assert all(marker not in surface for marker in _MARKERS)
    assert source["requests"] == []
    assert sink["requests"] == []
