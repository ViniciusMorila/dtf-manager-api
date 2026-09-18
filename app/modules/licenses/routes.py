"""Consulta autenticada da licença do titular do access token."""

from fastapi import APIRouter, Response

from app.modules.auth.dependencies import LicenseStatusDependency
from app.modules.subscriptions.schemas.license import LicenseStatus

router: APIRouter = APIRouter(prefix="/license", tags=["license"])


@router.get("/status", response_model=LicenseStatus)
def license_status(license: LicenseStatusDependency, response: Response) -> LicenseStatus:
    response.headers["Cache-Control"] = "no-store"
    return license
