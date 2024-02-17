import json

import llsd
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class LLSDMiddleware:
    def __init__(
        self,
        app: ASGIApp,
    ) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            responder = _LLSDResponder(self.app)
            await responder(scope, receive, send)
            return
        await self.app(scope, receive, send)


_CONTENT_TYPE_TO_PARSE = {
    "application/llsd+xml": llsd.parse_xml,
    "application/llsd+binary": llsd.parse_binary,
    "application/llsd+notation": llsd.parse_notation,
}

_CONTENT_TYPE_TO_FORMAT = {
    "application/llsd+xml": llsd.format_xml,
    "application/llsd+binary": llsd.format_binary,
    "application/llsd+notation": llsd.format_notation,
}


class _LLSDResponder:

    def __init__(
        self,
        app: ASGIApp,
    ) -> None:
        self.app = app
        self.should_decode_from_llsd_to_json = False
        self.should_encode_from_json_to_llsd = False
        self.receive: Receive = unattached_receive
        self.send: Send = unattached_send
        self.initial_message: Message = {}
        self.started = False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        headers = MutableHeaders(scope=scope)

        try:
            self.parse = _CONTENT_TYPE_TO_PARSE[headers.get("content-type")]
            self.should_decode_from_llsd_to_json = True
        except KeyError:
            self.should_decode_from_llsd_to_json = False

        try:
            self.accept_header = headers.get("accept")
            self.format = _CONTENT_TYPE_TO_FORMAT[self.accept_header]
            self.should_encode_from_json_to_llsd = True
        except KeyError:
            self.should_encode_from_json_to_llsd = False

        self.receive = receive
        self.send = send

        if self.should_decode_from_llsd_to_json:
            # We're going to present JSON content to the application,
            # so rewrite `Content-Type` for consistency and compliance
            # with possible downstream security checks in some frameworks.
            # See: https://github.com/florimondmanca/msgpack-asgi/issues/23
            headers["content-type"] = "application/json"

        await self.app(scope, self.receive_with_llsd, self.send_with_llsd)

    async def receive_with_llsd(self) -> Message:
        message = await self.receive()

        if not self.should_decode_from_llsd_to_json:
            return message

        assert message["type"] == "http.request"

        body = message["body"]
        more_body = message.get("more_body", False)
        if more_body:
            # Some implementations (e.g. HTTPX) may send one more empty-body message.
            # Make sure they don't send one that contains a body, or it means
            # that clients attempt to stream the request body.
            message = await self.receive()
            if message["body"] != b"":  # pragma: no cover
                raise NotImplementedError("Streaming the request body isn't supported yet")

        message["body"] = json.dumps(self.parse(body)).encode()

        return message

    async def send_with_llsd(self, message: Message) -> None:
        if not self.should_encode_from_json_to_llsd:
            await self.send(message)
            return

        if message["type"] == "http.response.start":
            headers = Headers(raw=message["headers"])
            if headers["content-type"] != "application/json":
                # Client accepts llsd, but the app did not send JSON data.
                # (Note that it may have sent llsd-encoded data.)
                self.should_encode_from_json_to_llsd = False
                await self.send(message)
                return

            # Don't send the initial message until we've determined how to
            # modify the ougoging headers correctly.
            self.initial_message = message

        elif message["type"] == "http.response.body":
            assert self.should_encode_from_json_to_llsd

            body = message.get("body", b"")
            more_body = message.get("more_body", False)
            if more_body:  # pragma: no cover
                raise NotImplementedError("Streaming the response body isn't supported yet")

            body = self.format(json.loads(body))

            headers = MutableHeaders(raw=self.initial_message["headers"])
            headers["Content-Type"] = self.accept_header
            headers["Content-Length"] = str(len(body))
            message["body"] = body

            await self.send(self.initial_message)
            await self.send(message)


async def unattached_receive() -> Message:
    raise RuntimeError("receive awaitable not set")  # pragma: no cover


async def unattached_send(message: Message) -> None:
    raise RuntimeError("send awaitable not set")  # pragma: no cover
