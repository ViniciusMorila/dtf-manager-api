"""Filtros de log que evitam gravar dados pessoais nos logs de acesso."""

import logging

ACCESS_LOGGER: str = "uvicorn.access"
REDACTED_QUERY: str = "?[omitido]"
_SENSITIVE_PREFIXES: tuple[str, ...] = ("/admin",)


class RedactAdminQueryFilter(logging.Filter):
    """Remove a query string das rotas /admin (a busca leva nome, e-mail ou telefone).

    O Uvicorn registra o acesso com args (cliente, método, caminho completo, versão, status).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            path: str = args[2]
            if "?" in path and path.startswith(_SENSITIVE_PREFIXES):
                record.args = (*args[:2], path.split("?", 1)[0] + REDACTED_QUERY, *args[3:])
        return True


def install_access_log_redaction() -> None:
    """Idempotente: create_app pode ser chamado várias vezes (testes, reload)."""
    logger: logging.Logger = logging.getLogger(ACCESS_LOGGER)
    if not any(isinstance(item, RedactAdminQueryFilter) for item in logger.filters):
        logger.addFilter(RedactAdminQueryFilter())
