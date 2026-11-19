"""Rate limit em memória por IP, em janela deslizante, para rotas sensíveis.

O estado vive no processo: com um único worker Uvicorn (configuração atual do
Railway) o limite é exato; com vários workers ou réplicas cada um conta separado.
"""

import threading
import time
from collections import deque
from collections.abc import Callable

from fastapi import Request

from app.core.config import Settings
from app.shared.exceptions.api import TooManyRequests

_MAX_TRACKED_KEYS: int = 50_000


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[tuple[str, str], deque[float]] = {}
        self._lock: threading.Lock = threading.Lock()

    def hit(self, scope: str, key: str, limit: int, window_seconds: int, now: float | None = None) -> int | None:
        """Registra a tentativa; devolve segundos para nova tentativa quando bloqueada."""
        current: float = time.monotonic() if now is None else now
        with self._lock:
            if len(self._hits) > _MAX_TRACKED_KEYS:
                self._prune(current, window_seconds)
            bucket: deque[float] = self._hits.setdefault((scope, key), deque())
            while bucket and bucket[0] <= current - window_seconds:
                bucket.popleft()
            if len(bucket) >= limit:
                return max(1, int(bucket[0] + window_seconds - current) + 1)
            bucket.append(current)
            return None

    def _prune(self, current: float, window_seconds: int) -> None:
        for key in [key for key, bucket in self._hits.items() if not bucket or bucket[-1] <= current - window_seconds]:
            del self._hits[key]

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter: RateLimiter = RateLimiter()


def client_address(request: Request, settings: Settings) -> str:
    """Usa o último salto de X-Forwarded-For (adicionado pelo proxy do Railway).

    O primeiro valor do cabeçalho é controlado pelo cliente e não é confiável.
    """
    if settings.rate_limit_trust_proxy:
        forwarded: list[str] = [part.strip() for part in request.headers.get("x-forwarded-for", "").split(",")]
        if forwarded and forwarded[-1]:
            return forwarded[-1]
    return request.client.host if request.client else "desconhecido"


def rate_limit(scope: str) -> Callable[[Request], None]:
    """Dependency FastAPI: recusa com 429 MUITAS_TENTATIVAS acima do limite."""

    def dependency(request: Request) -> None:
        settings: Settings = Settings()
        if not settings.rate_limit_enabled:
            return
        retry_after: int | None = limiter.hit(scope, client_address(request, settings),
                                              settings.rate_limit_requests, settings.rate_limit_window_seconds)
        if retry_after is not None:
            raise TooManyRequests(headers={"Retry-After": str(retry_after)})

    return dependency
