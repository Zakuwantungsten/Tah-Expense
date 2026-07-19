"""Shared asynchronous HTTP client for the desktop application's API domains."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from tahmeed.config import API_BASE_URL


class ApiError(RuntimeError):
    """A normalized error returned by the Tahmeed API."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str = "api_error",
        details: Any = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.details = details
        self.request_id = request_id


class ApiConnectionError(ApiError):
    pass


class ApiAuthenticationError(ApiError):
    pass


class ApiClient:
    def __init__(
        self,
        base_url: str = API_BASE_URL,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/") + "/"
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._refresh_lock = asyncio.Lock()

    @property
    def is_authenticated(self) -> bool:
        return bool(self._access_token and self._refresh_token)

    def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(15.0, connect=5.0),
                transport=self._transport,
                headers={"Accept": "application/json"},
            )
        return self._client

    def set_tokens(self, payload: dict[str, Any]) -> None:
        self._access_token = payload["access_token"]
        self._refresh_token = payload["refresh_token"]

    def clear_tokens(self) -> None:
        self._access_token = None
        self._refresh_token = None

    async def login(self, username: str, password: str) -> dict[str, Any]:
        self.clear_tokens()
        payload = await self.request(
            "POST",
            "v1/auth/login",
            auth=False,
            json={"username": username, "password": password},
        )
        self.set_tokens(payload)
        return payload

    async def _refresh(self, rejected_token: str | None) -> bool:
        async with self._refresh_lock:
            if self._access_token and self._access_token != rejected_token:
                return True
            token = self._refresh_token
            if not token:
                return False
            try:
                response = await self._http().post(
                    "v1/auth/refresh", json={"refresh_token": token}
                )
                if response.status_code >= 400:
                    self.clear_tokens()
                    return False
                self.set_tokens(response.json())
                return True
            except (httpx.HTTPError, ValueError, KeyError):
                self.clear_tokens()
                return False

    @staticmethod
    def _error(response: httpx.Response) -> ApiError:
        message = f"API request failed with status {response.status_code}"
        code = "http_error"
        details = None
        request_id = response.headers.get("x-request-id")
        try:
            body = response.json()
            data = body.get("error", body) if isinstance(body, dict) else {}
            if isinstance(data, dict):
                message = str(data.get("message") or message)
                code = str(data.get("code") or code)
                details = data.get("details")
                request_id = data.get("request_id") or request_id
        except ValueError:
            if response.text.strip():
                message = response.text.strip()[:500]
        error_type = ApiAuthenticationError if response.status_code == 401 else ApiError
        return error_type(
            message,
            status_code=response.status_code,
            code=code,
            details=details,
            request_id=request_id,
        )

    async def request(
        self,
        method: str,
        path: str,
        *,
        auth: bool = True,
        **kwargs: Any,
    ) -> Any:
        rejected_token = self._access_token
        headers = dict(kwargs.pop("headers", {}))
        if auth:
            if not rejected_token:
                raise ApiAuthenticationError(
                    "You are not signed in.", status_code=401, code="not_authenticated"
                )
            headers["Authorization"] = f"Bearer {rejected_token}"
        try:
            response = await self._http().request(method, path, headers=headers, **kwargs)
        except httpx.TimeoutException as exc:
            raise ApiConnectionError("The API request timed out.", code="timeout") from exc
        except httpx.RequestError as exc:
            raise ApiConnectionError(
                "Unable to connect to the Tahmeed API.", code="connection_error"
            ) from exc

        if auth and response.status_code == 401 and await self._refresh(rejected_token):
            headers["Authorization"] = f"Bearer {self._access_token}"
            try:
                response = await self._http().request(
                    method, path, headers=headers, **kwargs
                )
            except httpx.TimeoutException as exc:
                raise ApiConnectionError(
                    "The API request timed out.", code="timeout"
                ) from exc
            except httpx.RequestError as exc:
                raise ApiConnectionError(
                    "Unable to connect to the Tahmeed API.", code="connection_error"
                ) from exc

        if response.status_code >= 400:
            raise self._error(response)
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise ApiError(
                "The API returned an invalid response.",
                status_code=response.status_code,
                code="invalid_response",
            ) from exc

    async def logout(self) -> None:
        try:
            if self._access_token:
                await self.request("POST", "v1/auth/logout")
        finally:
            self.clear_tokens()

    async def close(self) -> None:
        self.clear_tokens()
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()


api_client = ApiClient()


async def close_api() -> None:
    await api_client.close()
