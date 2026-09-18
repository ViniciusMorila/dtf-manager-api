"""Projeção pública da conta, sem CPF, hashes ou relacionamentos internos."""

from uuid import UUID

from pydantic import EmailStr

from app.shared.schemas import OutputSchema, UTCDateTime


class UserRead(OutputSchema):
    id: UUID
    name: str
    email: EmailStr
    is_active: bool
    created_at: UTCDateTime
    updated_at: UTCDateTime
