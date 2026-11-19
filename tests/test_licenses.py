"""Ponta a ponta das licenças via HTTP com PostgreSQL real, revertido ao final.

Requer DATABASE_URL de um banco de TESTE com `alembic upgrade head` aplicado;
sem ela os testes são ignorados. Os testes de concorrência gravam e apagam
dados reais e só rodam quando o nome do banco contém "test".
"""

import asyncio
import secrets
import threading
from collections.abc import Generator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from alembic.migration import MigrationContext
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.session import get_engine, get_session
from app.main import create_app
from app.modules.auth.registration import register_user
from app.modules.licenses import admin_service
from app.modules.licenses.models import (
    ActivationKey,
    ActivationKeyStatus,
    License,
    LicensePlanCode,
    LicenseReset,
    LicenseState,
    TrialUsage,
)
from app.modules.licenses.schemas.contract import GerarChaveRequest
from app.modules.licenses.service import redeem_key
from app.modules.users.models import User, UserRole
from app.modules.users.schemas.input import UserCreate
from app.shared.exceptions.license import InvalidActivationKey, TrialAlreadyUsed
from app.shared.utils.calendar import add_calendar_months
from scripts.backfill_licenses import backfill_licenses
from scripts.seed_admin import seed_admin

HEAD: str = "0003_licenses"


@pytest.fixture
def environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    if Settings().database_url is None:
        pytest.skip("DATABASE_URL ausente: licenças no PostgreSQL não verificadas")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ENVIRONMENT", "testing")
    monkeypatch.setenv("JWT_ACCESS_SECRET", secrets.token_hex(32))
    monkeypatch.setenv("JWT_REFRESH_SECRET", secrets.token_hex(32))
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")


@pytest.fixture
def db(environment: None) -> Generator[Session, None, None]:
    """Commits dos serviços viram savepoints; tudo é revertido ao final."""
    with get_engine().connect() as connection:
        if MigrationContext.configure(connection).get_current_revision() != HEAD:
            pytest.skip(f"banco sem a migration {HEAD}: execute alembic upgrade head no banco de teste")
        connection.rollback()
        outer = connection.begin()
        try:
            with Session(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint") as session:
                yield session
        finally:
            outer.rollback()


def settle(session: Session) -> None:
    """Fecha a transação implícita de leituras do teste (cada requisição real usa sessão nova)."""
    if session.in_transaction():
        session.commit()


def random_phone() -> str:
    while True:
        subscriber: str = "9" + f"{secrets.randbelow(10**8):08d}"
        if len(set(subscriber)) > 1:
            return f"(11) {subscriber[:5]}-{subscriber[5:]}"


def random_machine() -> str:
    return f"PC-{uuid4().hex}"


@dataclass
class Account:
    email: str
    password: str
    phone: str
    machine: str
    token: str | None = None


class Api:
    def __init__(self, application: FastAPI, session: Session) -> None:
        self.application = application
        self.session = session

    def request(self, method: str, path: str, *, json: Any = None, token: str | None = None,
                headers: dict[str, str] | None = None) -> Response:
        settle(self.session)
        all_headers: dict[str, str] = dict(headers or {})
        if token:
            all_headers["Authorization"] = f"Bearer {token}"

        async def run() -> Response:
            async with AsyncClient(transport=ASGITransport(app=self.application), base_url="http://test") as client:
                return await client.request(method, path, json=json, headers=all_headers)

        return asyncio.run(run())

    def register(self, *, phone: str | None = None, machine: str | None = None) -> tuple[Account, Response]:
        account = Account(email=f"{uuid4().hex}@example.com", password=secrets.token_urlsafe(16) + "a1",
                          phone=phone or random_phone(), machine=machine or random_machine())
        response: Response = self.request("POST", "/auth/register", json={
            "nome": "Cliente Teste", "telefone": account.phone, "email": account.email,
            "senha": account.password, "id_maquina": account.machine})
        return account, response

    def login(self, account: Account, machine: str | None = "__same__") -> Response:
        body: dict[str, Any] = {"email": account.email, "senha": account.password}
        target: str | None = account.machine if machine == "__same__" else machine
        if target is not None:
            body["id_maquina"] = target
        response: Response = self.request("POST", "/auth/login", json=body)
        if response.status_code == 200:
            account.token = response.json()["access_token"]
        return response

    def status(self, account: Account, machine: str | None = None) -> Response:
        return self.request("GET", "/licencas/status", token=account.token,
                            headers={"X-Id-Maquina": machine or account.machine})

    def activate(self, account: Account, key: str, *, machine: str | None = None,
                 with_password: bool = False) -> Response:
        body: dict[str, Any] = {"chave": key, "id_maquina": machine or account.machine}
        if with_password:
            body.update(email=account.email, senha=account.password)
        return self.request("POST", "/licencas/ativar-chave", json=body,
                            token=None if with_password else account.token)

    def license_of(self, account: Account) -> License:
        settle(self.session)
        self.session.expire_all()
        license: License = self.session.scalars(select(License).join(User).where(User.email == account.email)).one()
        settle(self.session)
        return license

    def set_expiration(self, account: Account, when: datetime) -> None:
        settle(self.session)
        with self.session.begin():
            self.session.execute(update(License).where(
                License.user_id == select(User.id).where(User.email == account.email).scalar_subquery(),
            ).values(expires_at=when))


@pytest.fixture
def api(db: Session) -> Api:
    application: FastAPI = create_app()
    application.dependency_overrides[get_session] = lambda: db
    return Api(application, db)


@pytest.fixture
def admin(api: Api, db: Session) -> Account:
    account = Account(email=f"admin-{uuid4().hex}@example.com", password=secrets.token_urlsafe(16) + "a1",
                      phone="", machine="")
    settle(db)
    seed_admin(db, email=account.email, password=account.password, name="Admin Teste")
    response: Response = api.login(account, machine=None)
    assert response.status_code == 200, response.text
    return account


def new_key(api: Api, admin: Account, plan: str) -> str:
    response: Response = api.request("POST", "/admin/chaves", token=admin.token, json={
        "plano": plan, "nome_cliente": "Cliente Chave", "contato_cliente": "(11) 98888-7777"})
    assert response.status_code == 201, response.text
    return response.json()["chave"]


def error_code(response: Response) -> str:
    return response.json()["error"]["code"]


# --- Cadastro e trava de teste -----------------------------------------------------------

def test_registration_creates_bound_trial(api: Api) -> None:
    before: datetime = datetime.now(UTC)
    account, response = api.register()
    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == {"user", "subscription", "licenca"} and body["subscription"] is None
    assert body["licenca"]["plano"] == "TESTE" and body["licenca"]["status"] == "ATIVA"
    assert body["licenca"]["dias_restantes"] == 7
    license: License = api.license_of(account)
    assert license.machine_id == account.machine.lower()
    assert license.phone == "".join(ch for ch in account.phone if ch.isdigit())
    assert before + timedelta(days=7) <= license.expires_at <= datetime.now(UTC) + timedelta(days=7)
    trial: TrialUsage = api.session.scalars(select(TrialUsage).where(TrialUsage.machine_id == license.machine_id)).one()
    assert trial.phone == license.phone


def test_second_trial_on_same_machine_is_refused(api: Api) -> None:
    first, _ = api.register()
    _, response = api.register(machine=first.machine.upper())
    assert response.status_code == 409
    assert error_code(response) == "TESTE_JA_UTILIZADO"
    assert "utilizado" in response.json()["error"]["message"]


def test_second_trial_with_same_phone_on_other_machine_is_refused(api: Api) -> None:
    first, _ = api.register()
    digits: str = "".join(ch for ch in first.phone if ch.isdigit())
    _, response = api.register(phone=f"+55 {digits}")
    assert response.status_code == 409
    assert error_code(response) == "TESTE_JA_UTILIZADO"


def test_refused_trial_does_not_create_account(api: Api) -> None:
    first, _ = api.register()
    second, response = api.register(machine=first.machine)
    assert response.status_code == 409
    assert api.session.scalar(select(User.id).where(User.email == second.email)) is None


def test_trial_lock_survives_account_deletion(api: Api, db: Session) -> None:
    account, _ = api.register()
    settle(db)
    with db.begin():
        db.execute(delete(User).where(User.email == account.email))
    assert db.scalar(select(TrialUsage.user_id).where(TrialUsage.machine_id == account.machine.lower())) is None
    _, response = api.register(machine=account.machine)
    assert error_code(response) == "TESTE_JA_UTILIZADO"


def test_invalid_phone_code(api: Api) -> None:
    _, response = api.register(phone="(11) 1234")
    assert response.status_code == 422
    assert error_code(response) == "TELEFONE_INVALIDO"


# --- Login e máquina ---------------------------------------------------------------------

def test_login_on_bound_machine_returns_license_and_server_time(api: Api) -> None:
    account, _ = api.register()
    response: Response = api.login(account)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["licenca"]["plano"] == "TESTE" and body["licenca"]["dias_restantes"] == 7
    assert abs(datetime.fromisoformat(body["servidor_agora"]) - datetime.now(UTC)) < timedelta(minutes=1)
    status: Response = api.status(account)
    assert status.status_code == 200 and status.json()["licenca"]["status"] == "ATIVA"


def test_login_on_other_machine_is_refused_without_tokens(api: Api, db: Session) -> None:
    account, _ = api.register()
    response: Response = api.login(account, machine=random_machine())
    assert response.status_code == 403
    assert error_code(response) == "MAQUINA_DIFERENTE"
    assert "access_token" not in response.text
    api.login(account)
    status: Response = api.status(account, machine=random_machine())
    assert status.status_code == 403 and error_code(status) == "MAQUINA_DIFERENTE"


def test_login_without_machine_is_refused_for_common_user(api: Api) -> None:
    account, _ = api.register()
    response: Response = api.login(account, machine=None)
    assert response.status_code == 422 and error_code(response) == "ID_MAQUINA_INVALIDO"


def test_wrong_password_stays_generic(api: Api) -> None:
    account, _ = api.register()
    account.password += "x"
    response: Response = api.login(account)
    assert response.status_code == 401 and error_code(response) == "INVALID_CREDENTIALS"


# --- Expiração ---------------------------------------------------------------------------

def test_expiration_is_persisted_and_refused_on_login_and_status(api: Api) -> None:
    account, _ = api.register()
    api.login(account)
    api.set_expiration(account, datetime.now(UTC) - timedelta(minutes=1))
    status: Response = api.status(account)
    assert status.status_code == 403 and error_code(status) == "LICENCA_EXPIRADA"
    assert status.json()["licenca"]["status"] == "EXPIRADA"
    assert api.license_of(account).status == LicenseState.EXPIRADA
    login: Response = api.login(account)
    assert login.status_code == 403 and error_code(login) == "LICENCA_EXPIRADA"
    assert "access_token" not in login.text


# --- Chaves -------------------------------------------------------------------------------

def test_valid_key_renews_valid_license_from_current_expiration(api: Api, admin: Account) -> None:
    account, _ = api.register()
    api.login(account)
    previous: datetime = api.license_of(account).expires_at
    key: str = new_key(api, admin, "MENSAL")
    response: Response = api.activate(account, key.lower())
    assert response.status_code == 200, response.text
    assert response.json()["plano_ativado"] == "MENSAL"
    license: License = api.license_of(account)
    assert license.plan == LicensePlanCode.MENSAL and license.status == LicenseState.ATIVA
    assert license.expires_at == add_calendar_months(previous, 1)
    stored: ActivationKey = api.session.scalars(select(ActivationKey).where(ActivationKey.license_id == license.id)).one()
    assert stored.status == ActivationKeyStatus.USADA and stored.used_at is not None
    assert key not in (stored.key_hash, stored.key_suffix)


def test_key_on_expired_license_counts_from_now_using_password(api: Api, admin: Account) -> None:
    account, _ = api.register()
    api.set_expiration(account, datetime.now(UTC) - timedelta(days=30))
    assert error_code(api.login(account)) == "LICENCA_EXPIRADA"
    key: str = new_key(api, admin, "SEMESTRAL")
    before: datetime = datetime.now(UTC)
    response: Response = api.activate(account, key, with_password=True)
    assert response.status_code == 200, response.text
    license: License = api.license_of(account)
    assert add_calendar_months(before, 6) <= license.expires_at <= add_calendar_months(datetime.now(UTC), 6)
    assert api.login(account).status_code == 200


def test_lifetime_key_clears_expiration_and_protects_later_keys(api: Api, admin: Account) -> None:
    account, _ = api.register()
    api.login(account)
    response: Response = api.activate(account, new_key(api, admin, "VITALICIO"))
    assert response.status_code == 200
    licenca = response.json()["licenca"]
    assert licenca["vitalicia"] is True and licenca["data_expiracao"] is None and licenca["dias_restantes"] is None
    monthly: str = new_key(api, admin, "MENSAL")
    refused: Response = api.activate(account, monthly)
    assert refused.status_code == 409 and error_code(refused) == "LICENCA_JA_VITALICIA"
    listed = api.request("GET", "/admin/chaves?status=DISPONIVEL", token=admin.token).json()["itens"]
    assert any(item["chave_mascarada"].endswith(monthly[-4:]) for item in listed)


def test_nonexistent_used_and_cancelled_keys_are_invalid(api: Api, admin: Account) -> None:
    first, _ = api.register()
    second, _ = api.register()
    api.login(first)
    api.login(second)
    for bad in ("ABCD-EFGH-JKMN-PQRS", "nao-e-chave", ""):
        response: Response = api.activate(first, bad)
        assert response.status_code == 422 and error_code(response) == "CHAVE_INVALIDA", bad
    used: str = new_key(api, admin, "ANUAL")
    assert api.activate(first, used).status_code == 200
    reused: Response = api.activate(second, used)
    assert reused.status_code == 422 and error_code(reused) == "CHAVE_INVALIDA"
    cancelled: str = new_key(api, admin, "ANUAL")
    key_id: str = next(item["id"] for item in api.request(
        "GET", "/admin/chaves?status=DISPONIVEL&limite=200", token=admin.token).json()["itens"]
        if item["chave_mascarada"].endswith(cancelled[-4:]))
    assert api.request("POST", f"/admin/chaves/{key_id}/cancelar", token=admin.token).json()["status"] == "CANCELADA"
    refused: Response = api.activate(second, cancelled)
    assert refused.status_code == 422 and error_code(refused) == "CHAVE_INVALIDA"
    again: Response = api.request("POST", f"/admin/chaves/{key_id}/cancelar", token=admin.token)
    assert again.status_code == 409 and error_code(again) == "CHAVE_INVALIDA"


def test_key_activation_checks_machine(api: Api, admin: Account) -> None:
    account, _ = api.register()
    api.login(account)
    response: Response = api.activate(account, new_key(api, admin, "MENSAL"), machine=random_machine())
    assert response.status_code == 403 and error_code(response) == "MAQUINA_DIFERENTE"


# --- Bloqueio, reset e administração ------------------------------------------------------

def test_block_and_unblock(api: Api, admin: Account) -> None:
    account, _ = api.register()
    api.login(account)
    license_id: UUID = api.license_of(account).id
    blocked: Response = api.request("POST", f"/admin/licencas/{license_id}/bloquear", token=admin.token)
    assert blocked.status_code == 200 and blocked.json()["status"] == "BLOQUEADA"
    for response in (api.status(account), api.login(account)):
        assert response.status_code == 403 and error_code(response) == "LICENCA_BLOQUEADA"
    key_refused: Response = api.activate(account, new_key(api, admin, "MENSAL"), with_password=True)
    assert error_code(key_refused) == "LICENCA_BLOQUEADA"
    unblocked: Response = api.request("POST", f"/admin/licencas/{license_id}/desbloquear", token=admin.token)
    assert unblocked.json()["status"] == "ATIVA"
    assert api.login(account).status_code == 200


def test_reset_machine_rebinds_and_enforces_limit(api: Api, admin: Account, db: Session) -> None:
    account, _ = api.register()
    api.login(account)
    license_id: UUID = api.license_of(account).id
    path: str = f"/admin/licencas/{license_id}/resetar-maquina"
    for count in range(1, 4):
        response: Response = api.request("POST", path, token=admin.token, json={"forcar": False})
        assert response.status_code == 200, response.text
        assert response.json()["id_maquina"] is None and response.json()["contador_resets"] == count
        old_machine: str = account.machine
        account.machine = random_machine()
        assert api.login(account).status_code == 200
        assert error_code(api.login(account, machine=old_machine)) == "MAQUINA_DIFERENTE"
    limited: Response = api.request("POST", path, token=admin.token)
    assert limited.status_code == 409 and error_code(limited) == "LIMITE_RESETS"
    forced: Response = api.request("POST", path, token=admin.token, json={"forcar": True})
    assert forced.status_code == 200 and forced.json()["contador_resets"] == 4
    history = db.scalars(select(LicenseReset).where(LicenseReset.license_id == license_id)
                         .order_by(LicenseReset.created_at)).all()
    assert len(history) == 4 and [item.forced for item in history] == [False, False, False, True]
    assert history[0].previous_machine_id is not None


def test_machine_cannot_hold_active_licenses_of_two_accounts(api: Api, admin: Account) -> None:
    first, _ = api.register()
    second, _ = api.register()
    license_id: UUID = api.license_of(second).id
    api.request("POST", f"/admin/licencas/{license_id}/resetar-maquina", token=admin.token)
    response: Response = api.login(second, machine=first.machine)
    assert response.status_code == 409 and error_code(response) == "MAQUINA_EM_USO"


def test_admin_list_filters_search_and_manual_change(api: Api, admin: Account) -> None:
    account, _ = api.register()
    digits: str = "".join(ch for ch in account.phone if ch.isdigit())
    found = api.request("GET", f"/admin/licencas?busca={digits[-6:]}&plano=TESTE&status=ATIVA",
                        token=admin.token).json()
    assert found["total"] == 1 and found["itens"][0]["usuario"]["email"] == account.email
    license_id: str = found["itens"][0]["id"]
    expires: datetime = datetime(2030, 5, 1, tzinfo=UTC)
    changed: Response = api.request("PATCH", f"/admin/licencas/{license_id}", token=admin.token,
                                    json={"plano": "ANUAL", "data_expiracao": expires.isoformat()})
    assert changed.status_code == 200 and changed.json()["plano"] == "ANUAL"
    assert datetime.fromisoformat(changed.json()["data_expiracao"]) == expires
    lifetime = api.request("PATCH", f"/admin/licencas/{license_id}", token=admin.token, json={"plano": "VITALICIO"})
    assert lifetime.json()["data_expiracao"] is None
    invalid = api.request("PATCH", f"/admin/licencas/{license_id}", token=admin.token,
                          json={"plano": "VITALICIO", "data_expiracao": expires.isoformat()})
    assert invalid.status_code == 422 and error_code(invalid) == "ALTERACAO_INVALIDA"
    past = api.request("PATCH", f"/admin/licencas/{license_id}", token=admin.token,
                       json={"plano": "MENSAL", "data_expiracao": "2020-01-01T00:00:00Z"})
    assert past.json()["status"] == "EXPIRADA"
    assert api.request("GET", f"/admin/licencas?busca={uuid4().hex}", token=admin.token).json()["total"] == 0
    assert api.request("POST", f"/admin/licencas/{uuid4()}/bloquear", token=admin.token).status_code == 404


def test_admin_routes_denied_for_common_user(api: Api) -> None:
    account, _ = api.register()
    api.login(account)
    for method, path, body in (
        ("GET", "/admin/licencas", None), ("GET", "/admin/chaves", None),
        ("POST", "/admin/chaves", {"plano": "VITALICIO", "nome_cliente": "x", "contato_cliente": "y"}),
        ("POST", f"/admin/licencas/{uuid4()}/resetar-maquina", {"forcar": True}),
    ):
        response: Response = api.request(method, path, token=account.token, json=body)
        assert response.status_code == 403, path
        assert error_code(response) == "ACESSO_NEGADO"


def test_admin_has_lifetime_license_without_machine_lock(api: Api, admin: Account, db: Session) -> None:
    license: License = db.scalars(select(License).join(User).where(User.email == admin.email)).one()
    assert license.plan == LicensePlanCode.VITALICIO and license.machine_id is None
    assert db.scalar(select(User.role).where(User.email == admin.email)) == UserRole.ADMIN
    for machine in (random_machine(), random_machine()):
        assert api.status(admin, machine=machine).status_code == 200
    assert api.license_of(admin).machine_id is None
    settle(db)
    again = seed_admin(db, email=admin.email, password=admin.password, name="Outro nome")
    assert not again.created and not again.password_updated and not again.license_created


def test_seed_admin_promotes_existing_user_and_backfill(api: Api, db: Session) -> None:
    account, _ = api.register()
    settle(db)
    result = seed_admin(db, email=account.email, password=secrets.token_urlsafe(12) + "a1", name="x")
    assert not result.created and not result.password_updated
    assert api.license_of(account).plan == LicensePlanCode.VITALICIO
    settle(db)
    with db.begin():
        legacy: User = User(name="Antigo", email=f"{uuid4().hex}@example.com", password_hash="x", cpf=None)
        db.add(legacy)
    simulated = backfill_licenses(db, apply=False)
    assert legacy.email in [item.email for item in simulated]
    assert db.scalar(select(License.id).where(License.user_id == legacy.id)) is None
    settle(db)
    applied = backfill_licenses(db, apply=True)
    assert any(item.email == legacy.email and item.plan == LicensePlanCode.TESTE for item in applied)
    assert db.scalar(select(License.plan).where(License.user_id == legacy.id)) == LicensePlanCode.TESTE
    settle(db)
    assert legacy.email not in [item.email for item in backfill_licenses(db, apply=False)]


# --- Concorrência (dados reais, apagados ao final) ------------------------------------------

@pytest.fixture
def committing(environment: None) -> Generator[list[str], None, None]:
    url = get_engine().url
    if "test" not in (url.database or ""):
        pytest.skip("testes de concorrência só rodam em banco cujo nome contém 'test'")
    emails: list[str] = []
    yield emails
    with Session(get_engine()) as session, session.begin():
        ids = select(User.id).where(User.email.in_(emails)).scalar_subquery()
        session.execute(delete(ActivationKey).where(ActivationKey.created_by.in_(ids)))
        session.execute(delete(TrialUsage).where(TrialUsage.user_id.in_(ids)))
        session.execute(delete(User).where(User.email.in_(emails)))


def fresh_user(emails: list[str], machine: str | None = None) -> UserCreate:
    email: str = f"race-{uuid4().hex}@example.com"
    emails.append(email)
    return UserCreate(name="Corrida", email=email, password=secrets.token_urlsafe(12) + "a1",
                      phone="".join(ch for ch in random_phone() if ch.isdigit()), machine_id=machine or uuid4().hex)


def run_concurrently(*tasks: Any) -> list[Any]:
    barrier = threading.Barrier(len(tasks))
    results: list[Any] = [None] * len(tasks)

    def worker(index: int, task: Any) -> None:
        barrier.wait()
        try:
            results[index] = task()
        except Exception as exc:  # noqa: BLE001 - o teste inspeciona o tipo do erro
            results[index] = exc

    threads = [threading.Thread(target=worker, args=(index, task)) for index, task in enumerate(tasks)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    return results


def test_same_key_redeemed_concurrently_only_once(committing: list[str]) -> None:
    users = []
    for _ in range(2):
        data = fresh_user(committing)
        with Session(get_engine(), expire_on_commit=False) as session:
            users.append((register_user(data, session).user.id, data.machine_id))
    with Session(get_engine(), expire_on_commit=False) as session:
        key: str = admin_service.generate_key(session, admin_id=users[0][0], data=GerarChaveRequest(
            plano=LicensePlanCode.ANUAL, nome_cliente="Corrida", contato_cliente="corrida")).chave

    def redeem(user_id: UUID, machine: str) -> Any:
        with Session(get_engine(), expire_on_commit=False) as session:
            return redeem_key(session, user_id=user_id, is_admin=False, raw_key=key, machine_id=machine)

    results = run_concurrently(*(lambda u=u, m=m: redeem(u, m) for u, m in users))
    assert sum(not isinstance(item, Exception) for item in results) == 1
    assert sum(isinstance(item, InvalidActivationKey) for item in results) == 1


def test_concurrent_trials_on_same_machine_only_one_succeeds(committing: list[str]) -> None:
    machine: str = uuid4().hex
    attempts = [fresh_user(committing, machine) for _ in range(3)]

    def register(data: UserCreate) -> Any:
        with Session(get_engine(), expire_on_commit=False) as session:
            return register_user(data, session)

    results = run_concurrently(*(lambda d=d: register(d) for d in attempts))
    assert sum(not isinstance(item, Exception) for item in results) == 1
    assert sum(isinstance(item, TrialAlreadyUsed) for item in results) == 2
