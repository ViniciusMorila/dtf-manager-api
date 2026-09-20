"""Entrada ASGI: uvicorn app.main:app."""

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from app.core.config import Settings
from app.core.health import router as health_router
from app.db.base import load_models
from app.modules.auth.routes import router as auth_router
from app.modules.licenses.routes import router as license_router
from app.modules.payments.routes import checkout_router
from app.modules.payments.routes import router as payments_router
from app.modules.plans.routes import router as plans_router
from app.modules.subscriptions.service import LicenseStatusUnavailable
from app.modules.users.routes import router as users_router
from app.shared.exceptions.authentication import AuthenticationError
from app.shared.exceptions.handlers import (
    authentication_error_handler,
    license_error_handler,
    registration_error_handler,
    validation_error_handler,
)
from app.shared.exceptions.registration import RegistrationError


def create_app() -> FastAPI:
    """Valida as configurações antes de criar a aplicação ASGI."""
    settings: Settings = Settings()
    load_models()
    application: FastAPI = FastAPI(title=settings.app_name)
    application.include_router(health_router)
    application.include_router(auth_router)
    application.include_router(license_router)
    application.include_router(users_router)
    application.include_router(payments_router)
    application.include_router(checkout_router)
    application.include_router(plans_router)
    application.add_exception_handler(RegistrationError, registration_error_handler)
    application.add_exception_handler(AuthenticationError, authentication_error_handler)
    application.add_exception_handler(LicenseStatusUnavailable, license_error_handler)
    application.add_exception_handler(RequestValidationError, validation_error_handler)
    return application


app: FastAPI = create_app()
