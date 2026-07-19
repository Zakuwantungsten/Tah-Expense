import asyncio

import httpx

from tahmeed.services.api_client import (
    ApiAuthenticationError,
    ApiClient,
    ApiConnectionError,
)


def test_login_stores_tokens_and_authorizes_requests() -> None:
    async def scenario() -> None:
        seen_authorization = None

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal seen_authorization
            if request.url.path == "/v1/auth/login":
                return httpx.Response(
                    200,
                    json={"access_token": "access", "refresh_token": "refresh"},
                )
            seen_authorization = request.headers.get("authorization")
            return httpx.Response(200, json={"ok": True})

        client = ApiClient("https://api.test", transport=httpx.MockTransport(handler))
        await client.login("alice", "password")
        assert await client.request("GET", "v1/example") == {"ok": True}
        assert seen_authorization == "Bearer access"
        await client.close()

    asyncio.run(scenario())


def test_401_refreshes_once_and_retries_request() -> None:
    async def scenario() -> None:
        refreshes = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal refreshes
            if request.url.path == "/v1/auth/refresh":
                refreshes += 1
                return httpx.Response(
                    200,
                    json={"access_token": "new", "refresh_token": "rotated"},
                )
            if request.headers.get("authorization") == "Bearer old":
                return httpx.Response(401, json={"error": {"message": "expired"}})
            return httpx.Response(200, json={"ok": True})

        client = ApiClient("https://api.test", transport=httpx.MockTransport(handler))
        client.set_tokens({"access_token": "old", "refresh_token": "refresh"})
        results = await asyncio.gather(
            client.request("GET", "v1/a"),
            client.request("GET", "v1/b"),
        )
        assert results == [{"ok": True}, {"ok": True}]
        assert refreshes == 1
        await client.close()

    asyncio.run(scenario())


def test_failed_refresh_clears_session_and_normalizes_error() -> None:
    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v1/auth/refresh":
                return httpx.Response(401, json={"error": {"message": "expired"}})
            return httpx.Response(
                401,
                json={
                    "error": {
                        "code": "invalid_token",
                        "message": "Access denied",
                        "request_id": "request-1",
                    }
                },
            )

        client = ApiClient("https://api.test", transport=httpx.MockTransport(handler))
        client.set_tokens({"access_token": "old", "refresh_token": "refresh"})
        try:
            await client.request("GET", "v1/private")
            raise AssertionError("request should fail")
        except ApiAuthenticationError as exc:
            assert exc.code == "invalid_token"
            assert exc.request_id == "request-1"
        assert not client.is_authenticated
        await client.close()

    asyncio.run(scenario())


def test_transport_errors_are_normalized() -> None:
    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("offline", request=request)

        client = ApiClient("https://api.test", transport=httpx.MockTransport(handler))
        client.set_tokens({"access_token": "access", "refresh_token": "refresh"})
        try:
            await client.request("GET", "v1/private")
            raise AssertionError("request should fail")
        except ApiConnectionError as exc:
            assert exc.code == "connection_error"
        await client.close()

    asyncio.run(scenario())
