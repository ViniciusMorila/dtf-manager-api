"""Validação de contratos públicos, sem endpoints nem banco."""

import secrets
from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import BaseModel, ValidationError

from app.db.base import load_models
from app.modules.auth.schemas.input import LoginRequest, LogoutRequest, RefreshRequest
from app.modules.auth.schemas.output import LogoutResponse, TokenResponse
from app.modules.licenses.schemas.input import LicenseCheckRequest
from app.modules.licenses.schemas.output import LicenseRead
from app.modules.plans.models import Plan, PlanCode
from app.modules.plans.schemas.input import PlanSelection
from app.modules.plans.schemas.output import PlanRead
from app.modules.subscriptions.models import Subscription, SubscriptionStatus
from app.modules.subscriptions.schemas.input import SubscriptionCreate
from app.modules.subscriptions.schemas.output import SubscriptionRead
from app.modules.users.models import User
from app.modules.users.schemas.input import UserCreate
from app.modules.users.schemas.output import UserRead


def test_user_projection_excludes_private_attributes() -> None:
    load_models()
    now: datetime = datetime.now(UTC)
    private: str = secrets.token_hex(32)
    user: User = User(id=uuid4(), name="Ana", cpf="12345678901", email="ana@example.com",
                      password_hash=private, is_active=True, created_at=now, updated_at=now)
    result: UserRead = UserRead.model_validate(user)
    assert set(result.model_dump()) == {"id", "name", "email", "is_active", "created_at", "updated_at"}
    assert private not in result.model_dump_json()
    assert user.cpf not in result.model_dump_json()


def test_plan_and_subscription_projection() -> None:
    load_models()
    now: datetime = datetime.now(UTC)
    plan: Plan = Plan(id=uuid4(), code=PlanCode.LIFETIME, name="Vitalício", duration_months=None,
                      is_lifetime=True, is_active=True, created_at=now, updated_at=now)
    assert PlanRead.model_validate(plan).model_dump(mode="json")["code"] == "LIFETIME"
    subscription: Subscription = Subscription(
        id=uuid4(), plan_id=plan.id, user_id=uuid4(), status=SubscriptionStatus.PENDING,
        starts_at=None, expires_at=None, activated_at=None, cancelled_at=None,
        created_at=now, updated_at=now,
    )
    output: SubscriptionRead = SubscriptionRead.model_validate(subscription)
    assert output.status == "PENDING"
    assert output.expires_at is None
    assert "user_id" not in output.model_dump()
    assert "payments" not in output.model_dump()


def test_registration_and_password_serialization() -> None:
    password: str = secrets.token_urlsafe(24) + "a1"
    request: UserCreate = UserCreate(name=" Ana ", cpf="12345678909", email="ana@example.com",
                                    password=password, plan_code="MONTHLY")
    assert request.name == "Ana"
    assert request.password.get_secret_value() == password
    assert "password" not in request.model_dump()
    assert password not in repr(request)
    assert password not in request.model_dump_json()


@pytest.mark.parametrize("schema", [LoginRequest, UserCreate])
def test_email_validation(schema: type[BaseModel]) -> None:
    with pytest.raises(ValidationError):
        schema.model_validate({"email": "invalid", "password": secrets.token_urlsafe(),
                               **({"name": "Ana", "cpf": "12345678901", "plan_code": "ANNUAL"}
                                  if schema is UserCreate else {})})


@pytest.mark.parametrize("field", ["is_active", "password_hash", "token_hash", "jwt_access_secret", "status"])
def test_registration_rejects_internal_fields(field: str) -> None:
    with pytest.raises(ValidationError):
        UserCreate.model_validate({"name": "Ana", "cpf": "12345678901", "email": "ana@example.com",
                                   "password": secrets.token_urlsafe(), "plan_code": "ANNUAL", field: True})


@pytest.mark.parametrize("schema", [PlanSelection, SubscriptionCreate])
def test_plan_selection_cannot_activate_license(schema: type[BaseModel]) -> None:
    assert schema.model_validate({"plan_code": "ANNUAL"}).model_dump()["plan_code"] == "ANNUAL"
    with pytest.raises(ValidationError):
        schema.model_validate({"plan_code": "ANNUAL", "status": "ACTIVE"})


@pytest.mark.parametrize("schema", [RefreshRequest, LogoutRequest])
def test_refresh_credential_excluded(schema: type[BaseModel]) -> None:
    token: str = secrets.token_urlsafe(32)
    value: BaseModel = schema.model_validate({"refresh_token": token})
    assert value.model_dump() == {}
    assert token not in repr(value)


def test_authentication_delivers_only_explicit_session_data() -> None:
    access: str = secrets.token_urlsafe(32)
    refresh: str = secrets.token_urlsafe(32)
    result: TokenResponse = TokenResponse(access_token=access, refresh_token=refresh, expires_in=900)
    assert result.model_dump() == {"access_token": access, "refresh_token": refresh,
                                   "token_type": "bearer", "expires_in": 900}
    assert access not in repr(result)
    assert LogoutResponse().model_dump() == {"status": "ok"}


def test_license_requires_server_result_and_timezone() -> None:
    assert LicenseCheckRequest().model_dump() == {}
    with pytest.raises(ValidationError):
        LicenseCheckRequest.model_validate({"is_valid": True})
    checked: datetime = datetime(2026, 9, 6, 12, tzinfo=timezone(timedelta(hours=-3)))
    result: LicenseRead = LicenseRead(is_valid=False, plan_code=None, status=None, expires_at=None, checked_at=checked)
    assert result.checked_at.hour == 15
    assert result.checked_at.tzinfo is UTC
    with pytest.raises(ValidationError):
        LicenseRead.model_validate({**result.model_dump(), "checked_at": datetime(2026, 9, 6)})  # noqa: DTZ001 - rejection of naive datetime is intentional
    with pytest.raises(ValidationError):
        LicenseRead.model_validate({"plan_code": "LIFETIME", "status": "ACTIVE", "expires_at": None, "checked_at": checked})


@pytest.mark.parametrize("schema", [UserRead, PlanRead, SubscriptionRead, TokenResponse, LogoutResponse, LicenseRead])
def test_public_json_schema_has_no_internal_fields(schema: type[BaseModel]) -> None:
    properties = schema.model_json_schema(mode="serialization")["properties"]
    assert not {"password_hash", "token_hash", "jwt_access_secret", "jwt_refresh_secret",
                "password", "metadata", "payments", "refresh_tokens"}.intersection(properties)
