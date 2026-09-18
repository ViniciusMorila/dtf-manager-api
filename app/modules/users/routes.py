"""Dados públicos da própria conta autenticada."""

from fastapi import APIRouter, Response

from app.modules.auth.dependencies import CurrentUserDependency, LicenseStatusDependency
from app.modules.users.schemas.me import AccountSubscription, MeResponse

router: APIRouter = APIRouter(tags=["account"])


@router.get("/me", response_model=MeResponse)
def me(user: CurrentUserDependency, license: LicenseStatusDependency, response: Response) -> MeResponse:
    response.headers["Cache-Control"] = "no-store"
    return MeResponse(id=user.id, name=user.name, email=user.email,
        subscription=AccountSubscription(status=license.status, plan=license.plan,
            starts_at=license.starts_at, expires_at=license.expires_at))
