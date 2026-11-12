"""轻量 HTTP 封装：超时、重试、代理、JSON 与重定向解析。"""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from . import constants


class HttpError(Exception):
    def __init__(
        self,
        message: str,
        status: int | None = None,
        url: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.url = url
        self.headers = headers or {}


class NetworkError(Exception):
    pass


@dataclass
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes
    final_url: str

    @property
    def text(self) -> str:
        charset = "utf-8"
        content_type = self.headers.get("content-type", "")
        if "charset=" in content_type:
            charset = content_type.split("charset=", 1)[1].split(";", 1)[0].strip() or "utf-8"
        try:
            return self.body.decode(charset, errors="replace")
        except (LookupError, UnicodeError):
            return self.body.decode("utf-8", errors="replace")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        return None


def _opener(proxy: str | None = None, follow_redirects: bool = True) -> urllib.request.OpenerDirector:
    handlers: list[urllib.request.BaseHandler] = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    else:
        handlers.append(urllib.request.ProxyHandler())
    if not follow_redirects:
        handlers.append(_NoRedirect())
    handlers.append(urllib.request.HTTPSHandler())
    return urllib.request.build_opener(*handlers)


def request(
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    timeout: float | None = None,
    retries: int = constants.HTTP_RETRIES,
    proxy: str | None = None,
    follow_redirects: bool = True,
    data: bytes | None = None,
    max_bytes: int | None = None,
) -> HttpResponse:
    request_headers = {
        "User-Agent": constants.USER_AGENT,
        "Accept": "*/*",
    }
    if headers:
        request_headers.update(headers)
    req = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    opener = _opener(proxy, follow_redirects)
    timeout_value = timeout or constants.READ_TIMEOUT
    last_error: Exception | None = None

    for attempt in range(max(1, retries + 1)):
        try:
            with opener.open(req, timeout=timeout_value) as response:
                body = response.read(max_bytes) if max_bytes else response.read()
                return HttpResponse(
                    status=getattr(response, "status", 200),
                    headers={key.lower(): value for key, value in response.headers.items()},
                    body=body,
                    final_url=response.geturl(),
                )
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code in (408, 429, 500, 502, 503, 504) and attempt < retries:
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                _sleep_backoff(attempt, retry_after)
                continue
            error_headers = (
                {key.lower(): value for key, value in exc.headers.items()}
                if exc.headers
                else {}
            )
            raise HttpError(
                f"HTTP {exc.code}: {exc.reason}",
                exc.code,
                url,
                error_headers,
            ) from exc
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as exc:
            last_error = exc
            if attempt < retries:
                _sleep_backoff(attempt, None)
                continue
            reason = getattr(exc, "reason", exc)
            raise NetworkError(f"网络请求失败: {reason} ({url})") from exc

    if isinstance(last_error, urllib.error.HTTPError):
        raise HttpError(str(last_error), last_error.code, url)
    raise NetworkError(f"网络请求失败: {last_error} ({url})")


def _sleep_backoff(attempt: int, retry_after: str | None) -> None:
    delay = 0.0
    if retry_after:
        try:
            delay = min(30.0, max(0.0, float(retry_after)))
        except ValueError:
            delay = 0.0
    if not delay:
        delay = min(8.0, 1.0 * (2**attempt))
    if delay:
        time.sleep(delay)


def get_json(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: float | None = None,
    retries: int = constants.HTTP_RETRIES,
    proxy: str | None = None,
) -> Any:
    response = request(
        url,
        headers={"Accept": "application/json", **(headers or {})},
        timeout=timeout,
        retries=retries,
        proxy=proxy,
    )
    try:
        return json.loads(response.text)
    except ValueError as exc:
        raise HttpError(f"返回内容不是有效 JSON: {url}", response.status, url) from exc


def get_text(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: float | None = None,
    retries: int = constants.HTTP_RETRIES,
    proxy: str | None = None,
) -> str:
    return request(
        url,
        headers=headers,
        timeout=timeout,
        retries=retries,
        proxy=proxy,
    ).text


def head(
    url: str,
    timeout: float | None = None,
    retries: int = 0,
    proxy: str | None = None,
) -> HttpResponse:
    try:
        return request(url, method="HEAD", timeout=timeout, retries=retries, proxy=proxy)
    except HttpError as exc:
        if exc.status in (405, 501):
            return request(
                url,
                headers={"Range": "bytes=0-0"},
                timeout=timeout,
                retries=retries,
                proxy=proxy,
            )
        raise


def redirect_location(
    url: str,
    timeout: float | None = None,
    proxy: str | None = None,
) -> str | None:
    """返回 3xx 的 Location，不跟随重定向。"""
    try:
        response = request(
            url,
            timeout=timeout,
            retries=0,
            proxy=proxy,
            follow_redirects=False,
        )
        return response.headers.get("location")
    except HttpError as exc:
        # urllib 对 3xx 可能抛 HTTPError，透传 Location。
        return exc.headers.get("location")


def quote_url_path(path: str) -> str:
    return urllib.parse.quote(path, safe="/:?=&%#+ ,")
