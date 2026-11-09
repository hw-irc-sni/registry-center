# Copyright (c) 2026 Huawei Technologies Co., Ltd.
# All Rights Reserved.
#
# SPDX-License-Identifier: Apache-2.0
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

# middleware.py
import asyncio
import re
from fastapi import Request, status
from fastapi.responses import JSONResponse
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware



class ConnectionLimitMiddleware:
    """Limit active HTTP responses, including their entire streaming lifetime.

    This is an ASGI response quota, not a TCP socket count. Non-HTTP scopes pass
    through; completion, disconnect and cancellation all release the quota.
    """

    def __init__(self, app, max_connections: int):
        self.app = app
        self.max_connections = max_connections
        self.active_connections = 0
        self._lock = asyncio.Lock()

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        async with self._lock:
            if self.active_connections >= self.max_connections:
                logger.error(f"The server is at maximum connection capacity. ({self.max_connections})")
                rejected = JSONResponse(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    content={
                        "code": status.HTTP_503_SERVICE_UNAVAILABLE,
                        "message": f"The server is at maximum connection capacity. ({self.max_connections})"
                    }
                )
            else:
                rejected = None
                self.active_connections += 1

        if rejected is not None:
            return await rejected(scope, receive, send)

        try:
            await self.app(scope, receive, send)
        finally:
            async with self._lock:
                self.active_connections -= 1


class TimeoutMiddleware(BaseHTTPMiddleware):
    """Timeout control middleware"""

    def __init__(self, app, timeout_seconds: int):
        super().__init__(app)
        self.timeout_seconds = timeout_seconds

    async def dispatch(self, request: Request, call_next):
        try:
            # Apply timeout
            response = await asyncio.wait_for(
                call_next(request),
                timeout=self.timeout_seconds
            )
            return response
        except asyncio.TimeoutError:
            logger.error(f"Request processing timeout. ({self.timeout_seconds}s)")
            return JSONResponse(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                content={
                    "code": status.HTTP_504_GATEWAY_TIMEOUT,
                    "message": f"Request processing timeout. ({self.timeout_seconds}s)"
                }
            )


_PREFIX_SEGMENT = re.compile(r"^[A-Za-z0-9._~-]+$")


def normalize_path_prefix(value) -> str:
    """'' (disabled) or '/seg[/seg...]' without a trailing slash; anything else is a config error."""
    prefix = str(value or "").strip().rstrip("/")
    if not prefix:
        return ""
    segments = prefix.split("/")
    if segments[0] != "" or not all(_PREFIX_SEGMENT.match(s) for s in segments[1:]) or ".." in segments:
        raise ValueError(f"path.prefix must look like /name[/name...], got {value!r}")
    return prefix


class PathPrefixMiddleware:
    """Serve the whole application under an optional external path prefix as well.

    A gateway that forwards `/<prefix>/rest/...` unchanged (the ODA Canvas
    component gateway routes `/<componentName>/...` without a rewrite) reaches
    the same routes as an in-cluster client calling `/rest/...` directly. The
    prefix is removed before any other middleware runs, so authentication and
    route matching only ever see the unprefixed path.
    """

    def __init__(self, app, prefix: str):
        self.app = app
        self.prefix = normalize_path_prefix(prefix)
        self._raw_prefix = self.prefix.encode("ascii")

    async def __call__(self, scope, receive, send):
        if self.prefix and scope["type"] in ("http", "websocket"):
            path = scope.get("path", "")
            if path == self.prefix or path.startswith(self.prefix + "/"):
                scope = dict(scope)
                scope["path"] = path[len(self.prefix):] or "/"
                raw_path = scope.get("raw_path")
                if isinstance(raw_path, bytes) and raw_path.startswith(self._raw_prefix):
                    scope["raw_path"] = raw_path[len(self._raw_prefix):] or b"/"
        await self.app(scope, receive, send)
