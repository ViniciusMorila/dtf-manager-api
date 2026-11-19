"""Rate limit em memória, em janela deslizante, por IP e por conta.

O estado vive no processo: com um único worker Uvicorn (configuração atual do
Railway) o limite é exato; com vários workers ou réplicas cada um conta separado.
"""

import logging
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from ipaddress import IPv6Address, ip_address, ip_network

from fastapi import Request
from jwt.exceptions import InvalidTokenError

from app.core.config import Settings
from app.shared.exceptions.api import TooManyRequests
from app.shared.security.jwt import decode_refresh_token

logger: logging.Logger = logging.getLogger(__name__)
MAX_TRACKED_KEYS: int = 50_000
INVALID_ADDRESS_BUCKET: str = "endereco-invalido"


class RateLimiter:
    """Contadores por (escopo, chave). Acima do teto, descarta as chaves usadas há mais tempo."""

    def __init__(self, max_keys: int = MAX_TRACKED_KEYS) -> None:
        self._hits: OrderedDict[tuple[str, str], deque[float]] = OrderedDict()
        self._lock: threading.Lock = threading.Lock()
        self._max_keys: int = max_keys

    def _bucket(self, scope: str, key: str, window_seconds: int, current: float) -> deque[float]:
        bucket: deque[float] = self._hits.setdefault((scope, key), deque())
        self._hits.move_to_end((scope, key))
        while bucket and bucket[0] <= current - window_seconds:
            bucket.popleft()
        while len(self._hits) > self._max_keys:
            self._hits.popitem(last=False)
        return bucket

    @staticmethod
    def _retry_after(bucket: deque[float], window_seconds: int, current: float) -> int:
        return max(1, int(bucket[0] + window_seconds - current) + 1)

    def hit(self, scope: str, key: str, limit: int, window_seconds: int, now: float | None = None) -> int | None:
        """Conta a tentativa; devolve segundos para nova tentativa quando já estava no limite."""
        current: float = time.monotonic() if now is None else now
        with self._lock:
            bucket: deque[float] = self._bucket(scope, key, window_seconds, current)
            if len(bucket) >= limit:
                return self._retry_after(bucket, window_seconds, current)
            bucket.append(current)
            return None

    def blocked(self, scope: str, key: str, limit: int, window_seconds: int, now: float | None = None) -> int | None:
        """Só consulta, sem contar (usado antes de verificar uma senha)."""
        current: float = time.monotonic() if now is None else now
        with self._lock:
            bucket: deque[float] = self._bucket(scope, key, window_seconds, current)
            return self._retry_after(bucket, window_seconds, current) if len(bucket) >= limit else None

    def record(self, scope: str, key: str, window_seconds: int, now: float | None = None) -> None:
        """Conta uma tentativa sem decidir (usado depois de uma senha errada)."""
        current: float = time.monotonic() if now is None else now
        with self._lock:
            self._bucket(scope, key, window_seconds, current).append(current)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()

    def __len__(self) -> int:
        return len(self._hits)


limiter: RateLimiter = RateLimiter()


def address_bucket(address: str) -> str:
    """IPv4 inteiro; IPv6 agrupado por /64 (um único cliente costuma ter o bloco todo)."""
    try:
        parsed = ip_address(address.strip())
    except ValueError:
        # Valores inválidos compartilham um único contador: trocar o texto não gera contadores novos.
        return INVALID_ADDRESS_BUCKET
    if isinstance(parsed, IPv6Address):
        if parsed.ipv4_mapped is not None:
            return str(parsed.ipv4_mapped)
        return str(ip_network(f"{parsed}/64", strict=False))
    return str(parsed)


def forwarded_values(request: Request) -> list[str]:
    """Todos os valores de X-Forwarded-For, de todas as linhas do cabeçalho, na ordem recebida."""
    return [part.strip() for line in request.headers.getlist("x-forwarded-for")
            for part in line.split(",") if part.strip()]


def client_address(request: Request, settings: Settings) -> str:
    """Com RATE_LIMIT_TRUST_PROXY, usa o último valor de X-Forwarded-For (o que o proxy adicionou).

    Os valores anteriores são controlados pelo cliente. Sem a variável, usa o IP
    da conexão TCP; atrás de um proxy isso é o IP do proxy (todos dividem o limite).
    """
    address: str = request.client.host if request.client else "desconhecido"
    if settings.rate_limit_trust_proxy:
        values: list[str] = forwarded_values(request)
        if values:
            address = values[-1]
    return address_bucket(address)


def _log_diagnostic(request: Request, settings: Settings, bucket: str) -> None:
    """Opt-in temporário (RATE_LIMIT_DIAGNOSTICS) para conferir o cabeçalho no Railway; registra IPs."""
    logger.warning(
        "event=rate_limit_client_ip path=%s xff_header_lines=%d xff_values=%s tcp_client=%s "
        "trust_proxy=%s bucket=%s",
        request.url.path, len(request.headers.getlist("x-forwarded-for")), forwarded_values(request),
        request.client.host if request.client else None, settings.rate_limit_trust_proxy, bucket,
    )


def _raise_if_limited(retry_after: int | None) -> None:
    if retry_after is not None:
        raise TooManyRequests(headers={"Retry-After": str(retry_after)})


def rate_limit(scope: str, *, strict: bool = False) -> Callable[[Request], None]:
    """Dependency por IP, executada antes da validação do corpo.

    strict=True usa os limites mais restritos do cadastro.
    """

    def dependency(request: Request) -> None:
        settings: Settings = Settings()
        if not settings.rate_limit_enabled:
            return
        bucket: str = client_address(request, settings)
        if settings.rate_limit_diagnostics:
            _log_diagnostic(request, settings, bucket)
        limit, window = ((settings.rate_limit_register_requests, settings.rate_limit_register_window_seconds)
                         if strict else (settings.rate_limit_requests, settings.rate_limit_window_seconds))
        _raise_if_limited(limiter.hit(scope, bucket, limit, window))

    return dependency


def session_rate_limit(scope: str) -> Callable[[Request], None]:
    """Por IP e por rota, com limite generoso, para refresh, logout e status da licença."""

    def dependency(request: Request) -> None:
        settings: Settings = Settings()
        if not settings.rate_limit_enabled:
            return
        bucket: str = client_address(request, settings)
        if settings.rate_limit_diagnostics:
            _log_diagnostic(request, settings, bucket)
        _raise_if_limited(limiter.hit(scope, bucket, settings.rate_limit_session_requests,
                                      settings.rate_limit_session_window_seconds))

    return dependency


def limit_user(scope: str, user_id: object) -> None:
    """Por usuário e por rota, de qualquer IP; chamado depois de identificar o usuário."""
    settings: Settings = Settings()
    if settings.rate_limit_enabled:
        _raise_if_limited(limiter.hit(f"user_{scope}", str(user_id), settings.rate_limit_user_requests,
                                      settings.rate_limit_user_window_seconds))


def limit_refresh_token_owner(scope: str, refresh_token: str) -> None:
    """Limite por usuário usando o dono de um refresh token com assinatura válida.

    Token inválido ou expirado não identifica ninguém e fica só com o limite por IP;
    o usuário vem da assinatura, então não dá para gastar o limite de outra conta.
    """
    settings: Settings = Settings()
    if not settings.rate_limit_enabled:
        return
    try:
        subject: str = decode_refresh_token(refresh_token, settings=settings)["sub"]
    except (InvalidTokenError, ValueError):
        return
    limit_user(scope, subject)


def limit_registration_email(email: str) -> None:
    """Tentativas de cadastro com o mesmo e-mail, contadas de qualquer IP."""
    settings: Settings = Settings()
    if settings.rate_limit_enabled:
        _raise_if_limited(limiter.hit("auth_register_email", email.lower(), settings.rate_limit_register_email_requests,
                                      settings.rate_limit_register_window_seconds))


ACCOUNT_SCOPE: str = "password_failures"


def ensure_account_not_limited(email: str) -> None:
    """Antes de verificar a senha: recusa se a conta acumulou falhas, de qualquer IP ou rota."""
    settings: Settings = Settings()
    if settings.rate_limit_enabled:
        _raise_if_limited(limiter.blocked(ACCOUNT_SCOPE, email.lower(), settings.rate_limit_account_requests,
                                          settings.rate_limit_account_window_seconds))


def record_password_failure(email: str) -> None:
    settings: Settings = Settings()
    if settings.rate_limit_enabled:
        limiter.record(ACCOUNT_SCOPE, email.lower(), settings.rate_limit_account_window_seconds)
