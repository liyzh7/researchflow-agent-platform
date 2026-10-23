"""Bound untrusted request bytes before JSON/multipart parsing, including chunked bodies."""

from starlette.responses import JSONResponse


class BodyLimitMiddleware:
    def __init__(self, app, upload_max_bytes):
        self.app = app
        self.upload_max_bytes = upload_max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            return await self.app(scope, receive, send)
        limit = self.upload_max_bytes + 65536 if scope["path"] == "/api/v1/documents" else 65536
        headers = dict(scope.get("headers", []))
        claimed = headers.get(b"content-length", b"0")
        try:
            too_large = int(claimed) > limit
        except ValueError:
            too_large = True
        chunks = []
        size = 0
        while not too_large:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            content = message.get("body", b"")
            size += len(content)
            if size > limit:
                too_large = True
                break
            chunks.append(content)
            if not message.get("more_body", False):
                break
        if too_large:
            return await JSONResponse(
                status_code=413,
                content={
                    "error": {
                        "code": "request_too_large",
                        "message": "Request body exceeds the local limit.",
                        "details": [],
                    }
                },
            )(scope, receive, send)
        body = b"".join(chunks)
        consumed = False

        async def bounded_receive():
            nonlocal consumed
            if not consumed:
                consumed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, bounded_receive, send)
