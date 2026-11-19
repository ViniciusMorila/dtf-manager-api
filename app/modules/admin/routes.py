"""Rotas administrativas; todas exigem Bearer de uma conta com perfil ADMIN."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response, status

from app.db.session import SessionDependency
from app.modules.auth.dependencies import AdminDependency
from app.modules.auth.schemas.registration import RegistrationErrorResponse
from app.modules.licenses import admin_service
from app.modules.licenses.models import (
    ActivationKeyStatus,
    LicensePlanCode,
    LicenseState,
)
from app.modules.licenses.schemas.contract import (
    AlterarLicencaRequest,
    ChaveAdmin,
    ChaveGerada,
    GerarChaveRequest,
    LicencaAdmin,
    ListaChaves,
    ListaLicencas,
    ResetMaquinaRequest,
)

router: APIRouter = APIRouter(
    prefix="/admin", tags=["admin"],
    responses={code: {"model": RegistrationErrorResponse} for code in (401, 403, 404, 409, 422, 503)},
)
Search = Annotated[str | None, Query(max_length=200)]
Limit = Annotated[int, Query(ge=1, le=admin_service.MAX_PAGE)]
Offset = Annotated[int, Query(ge=0)]


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


@router.post("/chaves", response_model=ChaveGerada, status_code=status.HTTP_201_CREATED)
def gerar_chave(data: GerarChaveRequest, admin: AdminDependency, session: SessionDependency,
                response: Response) -> ChaveGerada:
    _no_store(response)
    return admin_service.generate_key(session, admin_id=admin.id, data=data)


@router.get("/chaves", response_model=ListaChaves)
def listar_chaves(admin: AdminDependency, session: SessionDependency, response: Response,
                  status_filter: Annotated[ActivationKeyStatus | None, Query(alias="status")] = None,
                  plano: LicensePlanCode | None = None, busca: Search = None,
                  limite: Limit = 50, deslocamento: Offset = 0) -> ListaChaves:
    _no_store(response)
    return admin_service.list_keys(session, status=status_filter, plan=plano, search=busca,
                                   limit=limite, offset=deslocamento)


@router.post("/chaves/{chave_id}/cancelar", response_model=ChaveAdmin)
def cancelar_chave(chave_id: UUID, admin: AdminDependency, session: SessionDependency,
                   response: Response) -> ChaveAdmin:
    _no_store(response)
    return admin_service.cancel_key(session, chave_id)


@router.get("/licencas", response_model=ListaLicencas)
def listar_licencas(admin: AdminDependency, session: SessionDependency, response: Response,
                    status_filter: Annotated[LicenseState | None, Query(alias="status")] = None,
                    plano: LicensePlanCode | None = None, busca: Search = None,
                    limite: Limit = 50, deslocamento: Offset = 0) -> ListaLicencas:
    _no_store(response)
    return admin_service.list_licenses(session, status=status_filter, plan=plano, search=busca,
                                       limit=limite, offset=deslocamento)


@router.post("/licencas/{licenca_id}/bloquear", response_model=LicencaAdmin)
def bloquear(licenca_id: UUID, admin: AdminDependency, session: SessionDependency,
             response: Response) -> LicencaAdmin:
    _no_store(response)
    return admin_service.block_license(session, licenca_id)


@router.post("/licencas/{licenca_id}/desbloquear", response_model=LicencaAdmin)
def desbloquear(licenca_id: UUID, admin: AdminDependency, session: SessionDependency,
                response: Response) -> LicencaAdmin:
    _no_store(response)
    return admin_service.unblock_license(session, licenca_id)


@router.post("/licencas/{licenca_id}/resetar-maquina", response_model=LicencaAdmin)
def resetar_maquina(licenca_id: UUID, admin: AdminDependency, session: SessionDependency, response: Response,
                    data: ResetMaquinaRequest | None = None) -> LicencaAdmin:
    _no_store(response)
    return admin_service.reset_machine(session, license_id=licenca_id, admin_id=admin.id,
                                       force=data.forcar if data is not None else False)


@router.patch("/licencas/{licenca_id}", response_model=LicencaAdmin)
def alterar_licenca(licenca_id: UUID, data: AlterarLicencaRequest, admin: AdminDependency,
                    session: SessionDependency, response: Response) -> LicencaAdmin:
    _no_store(response)
    return admin_service.update_license(session, license_id=licenca_id, data=data)
