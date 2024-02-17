from typing import Any, Callable

import httpx
import llsd
import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.types import Receive, Scope, Send

from llsd_asgi import LLSDMiddleware
from tests.utils import mock_receive, mock_send

Format = Callable[[Any], bytes]
Parse = Callable[[bytes], Any]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content_type,format",
    [
        ("application/llsd+xml", llsd.format_xml),
        ("application/llsd+binary", llsd.format_binary),
        ("application/llsd+notation", llsd.format_notation),
    ],
)
async def test_llsd_request(content_type: str, format: Format) -> None:
    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        request = Request(scope, receive=receive)
        content_type = request.headers["content-type"]
        data = await request.json()
        message = data["message"]
        text = f"content_type={content_type!r} message={message!r}"

        response = PlainTextResponse(text)
        await response(scope, receive, send)

    app = LLSDMiddleware(app)

    async with httpx.AsyncClient(app=app, base_url="http://testserver") as client:
        content = {"message": "Hello, world!"}
        body = format(content)
        r = await client.post("/", content=body, headers={"content-type": content_type})
        assert r.status_code == 200
        assert r.text == "content_type='application/json' message='Hello, world!'"


@pytest.mark.asyncio
async def test_non_llsd_request() -> None:
    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        request = Request(scope, receive=receive)
        content_type = request.headers["content-type"]
        message = (await request.body()).decode()
        text = f"content_type={content_type!r} message={message!r}"

        response = PlainTextResponse(text)
        await response(scope, receive, send)

    app = LLSDMiddleware(app)

    async with httpx.AsyncClient(app=app, base_url="http://testserver") as client:
        r = await client.post(
            "/",
            content="Hello, world!",
            headers={"content-type": "text/plain"},
        )
        assert r.status_code == 200
        assert r.text == "content_type='text/plain' message='Hello, world!'"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "header,format,parse",
    [
        ("application/llsd+xml", llsd.format_xml, llsd.parse_xml),
        ("application/llsd+binary", llsd.format_binary, llsd.parse_binary),
        ("application/llsd+notation", llsd.format_notation, llsd.parse_notation),
    ],
)
async def test_llsd_accepted(header: str, format: Format, parse: Parse) -> None:
    app = LLSDMiddleware(JSONResponse({"message": "Hello, world!"}))

    async with httpx.AsyncClient(app=app, base_url="http://testserver") as client:
        r = await client.get("/", headers={"accept": header})
        assert r.status_code == 200
        assert r.headers["content-type"] == header
        expected_data = {"message": "Hello, world!"}
        assert int(r.headers["content-length"]) == len(format(expected_data))
        assert parse(r.content) == expected_data


@pytest.mark.asyncio
async def test_llsd_accepted_but_response_is_not_json() -> None:
    app = LLSDMiddleware(PlainTextResponse("Hello, world!"))

    async with httpx.AsyncClient(app=app, base_url="http://testserver") as client:
        r = await client.get("/", headers={"accept": "application/llsd+xml"})
        assert r.status_code == 200
        assert r.headers["content-type"] == "text/plain; charset=utf-8"
        assert r.text == "Hello, world!"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "header,format,parse",
    [
        ("application/llsd+xml", llsd.format_xml, llsd.parse_xml),
        ("application/llsd+binary", llsd.format_binary, llsd.parse_binary),
        ("application/llsd+notation", llsd.format_notation, llsd.parse_notation),
    ],
)
async def test_llsd_accepted_and_response_is_already_llsd(header: str, format: Format, parse: Parse) -> None:
    data = format({"message": "Hello, world!"})
    response = Response(data, media_type=header)
    app = LLSDMiddleware(response)

    async with httpx.AsyncClient(app=app, base_url="http://testserver") as client:
        r = await client.get("/", headers={"accept": header})
        assert r.status_code == 200
        assert r.headers["content-type"] == header
        expected_data = {"message": "Hello, world!"}
        assert int(r.headers["content-length"]) == len(format(expected_data))
        assert parse(r.content) == expected_data


@pytest.mark.asyncio
async def test_llsd_not_accepted() -> None:
    app = LLSDMiddleware(JSONResponse({"message": "Hello, world!"}))

    async with httpx.AsyncClient(app=app, base_url="http://testserver") as client:
        r = await client.get("/")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/json"
        assert r.json() == {"message": "Hello, world!"}
        with pytest.raises(llsd.LLSDParseError):
            llsd.parse_xml(r.content)


@pytest.mark.asyncio
async def test_request_is_not_http() -> None:
    async def lifespan_only_app(scope: Scope, receive: Receive, send: Send) -> None:
        assert scope["type"] == "lifespan"

    app = LLSDMiddleware(lifespan_only_app)
    scope = {"type": "lifespan"}
    await app(scope, mock_receive, mock_send)
