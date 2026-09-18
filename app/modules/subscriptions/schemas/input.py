"""Solicitação de assinatura: ativação e vigência pertencem à API."""

from app.modules.plans.schemas.input import PlanSelection


class SubscriptionCreate(PlanSelection):
    """Usuário será obtido da sessão; status não pode ser enviado pelo cliente."""
