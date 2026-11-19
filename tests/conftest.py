"""Fixtures compartilhadas pela suíte."""

from collections.abc import Generator

import pytest

from app.shared.security.rate_limit import limiter


@pytest.fixture(autouse=True)
def reset_rate_limit() -> Generator[None, None, None]:
    """O limitador vive no processo; cada teste começa sem tentativas registradas."""
    limiter.reset()
    yield
    limiter.reset()
