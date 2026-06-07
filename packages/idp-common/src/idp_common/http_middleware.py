import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

logger = structlog.get_logger()


class HttpAccessLogMiddleware(BaseHTTPMiddleware):
    """Log client errors (4xx/5xx) with request context for production debugging."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        if response.status_code >= 400:
            logger.warning(
                "http_response",
                method=request.method,
                path=request.url.path,
                query=str(request.query_params) if request.query_params else None,
                status_code=response.status_code,
                client_ip=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent"),
            )
        return response
