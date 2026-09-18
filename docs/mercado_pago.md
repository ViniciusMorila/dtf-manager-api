# Mercado Pago — etapa 18

SDK oficial `mercadopago>=3.5,<4`, validado com 3.5.0. `simplejson` permite
serializar dinheiro em JSON numérico exato. O transporte injetado no SDK usa
Decimal na escrita/leitura de valores; nenhum cálculo monetário usa float.

## Configuração

Configurar por ambiente ou `.env` local, nunca versionado:

- `MERCADO_PAGO_ACCESS_TOKEN`: credencial do servidor para a aplicação Mercado Pago.
- `MERCADO_PAGO_WEBHOOK_SECRET`: assinatura secreta obtida no painel de Webhooks.
- `MERCADO_PAGO_ENVIRONMENT`: `test` ou `production`; `live_mode` da resposta deve corresponder.

Configuração ausente/incompleta bloqueia operações de pagamento. Em production,
configuração parcial ou fictícia impede startup. As três variáveis ausentes
mantêm a integração desabilitada. Não misturar bancos/credenciais de teste e produção.

Após configurar DATABASE_URL, executar no PowerShell:

```powershell
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m alembic upgrade head
```

A migration `0002_plan_price` adiciona `plans.price NUMERIC(18,2)` em BRL.
Planos existentes ficam com preço nulo. Defina preços reais pela administração
interna do servidor/banco antes de cobrar. O seed não inventa nem sobrescreve preços.
Preço ausente, não positivo ou divergente bloqueia cobrança. Não alterar preço de
plano com cobrança pendente: a conciliação exige preço atual igual ao valor persistido.
Versione os planos ao alterar preços em uma evolução futura.

No painel **Suas integrações → aplicação → Webhooks**, configurar URL HTTPS pública
`https://SEU-DOMINIO/payments/mercado-pago/webhook` para eventos de **Pagamentos** no
ambiente correspondente. Copiar a assinatura secreta para o ambiente da API.
Habilitar Pix na conta recebedora conforme requisitos do Mercado Pago. Nenhuma
credencial, configuração de painel ou cobrança real foi criada nesta implementação.

## Uso interno e idempotência

```python
from decimal import Decimal
from uuid import UUID
from sqlalchemy.orm import Session
from app.core.config import Settings
from app.modules.payments.mercado_pago import MercadoPagoPaymentProvider
from app.modules.payments.service import PaymentService
from app.modules.payments.contracts import PaymentRecord

def cobrar(session: Session, user_id: UUID, subscription_id: UUID,
           preco_servidor: Decimal) -> PaymentRecord:
    return PaymentService(session).create_provider_charge(
        user_id=user_id, subscription_id=subscription_id,
        expected_amount=preco_servidor, description="DTF Manager",
        provider=MercadoPagoPaymentProvider(Settings()),
    )
```

O serviço recarrega usuário, assinatura e plano, verificando vínculos e exigindo
expected_amount igual a `plan.price`. Não existe endpoint que aceite preço do cliente.

Esta etapa oferece uma cobrança Pix inicial por assinatura/provedor, sem renovação
ou nova tentativa comercial após rejeição/cancelamento. O UUID da cobrança é estável
por assinatura/provedor, independente de CPF/email. A intenção PENDING e o corpo da
cobrança são persistidos antes da rede. Retry após timeout/reinício reutiliza esse UUID
como `x-idempotency-key` e o corpo persistido; se já houver ID externo, consulta em vez
de criar. O email necessário para o Pix fica apenas no metadata interno da intenção,
nunca no external_reference, nos logs ou schemas públicos.

Falha após criação remota pode ser conciliada pelo webhook: metadata contém UUID local
do pagamento e external_reference contém UUID da assinatura. Não apagar intenções
pendentes para tentar novamente. Entrega de QR Code/checkout público fica para etapa futura.

## Webhook e confirmação

`POST /payments/mercado-pago/webhook` não exige JWT. Exige `x-signature`, `x-request-id`
e exatamente um `data.id` na query. A assinatura HMAC é validada pelo SDK com manifesto
oficial; o body não é fonte de ID/status. Consulta `/v1/payments/{id}` com credencial do
servidor. Valida ID externo/local, referência, usuário da assinatura, preço, moeda e ambiente.

Somente PaymentService aplica APPROVED e ativa na mesma transação, sob locks.
Repetições não estendem a assinatura. Não há filtro de idade do timestamp do webhook:
reentregas são autenticadas e reconciliadas com o estado atual consultado no provedor.

- 200: conciliação confirmada e commit concluído.
- 401: assinatura inválida, inclusive hash não ASCII ou request-id vazio/em branco.
- 409: pagamento inexistente no provedor ou no banco local, ou incompatível;
  nenhuma aprovação aplicada.
- 503: indisponibilidade temporária; a reentrega pode tentar novamente.

Sem ACK antecipado ou tarefa em memória. Timeout externo de 8 segundos por chamada,
sem retries ocultos no transporte. Logs contêm somente categorias de diagnóstico,
sem body, headers, credenciais ou secrets. Reembolso/chargeback mapeiam para REFUNDED;
política de revogação de licença por reembolso não é alterada nesta etapa.

## Validação

```powershell
.venv\Scripts\python.exe -m pytest -q
```

Testes em `tests/test_mercado_pago.py` simulam SDK/transporte, estados, assinatura,
referências, preços, falhas, retries e duplicidades. Nenhuma cobrança real.
Testes PostgreSQL exigem DATABASE_URL em banco de testes com migrations aplicadas;
nunca usar banco de produção para testes. Sem DATABASE_URL, a migration só pode ser
validada offline e os testes PostgreSQL são ignorados.

Fontes oficiais:
[SDK Python](https://github.com/mercadopago/sdk-python) e
[Webhooks](https://www.mercadopago.com.br/developers/en/docs/wix/additional-content/your-integrations/notifications/webhooks).

## Revisão da etapa 18 e roteiro de teste

A revisão exige BRL e referências locais não nulas também na resposta de criação,
antes de encaminhar a confirmação ao PaymentService. Testes exercitam o webhook e
serviços reais com persistência/SDK simulados em 2, 5 e 10 entregas, incluindo
APPROVED, PENDING e REJECTED. A rede de pagamentos fica bloqueada por fixture.

Preencher manualmente DATABASE_URL de um banco de testes, JWT_ACCESS_SECRET e
JWT_REFRESH_SECRET aleatórios/distintos, além das três variáveis Mercado Pago.
Usar ENVIRONMENT=testing e MERCADO_PAGO_ENVIRONMENT=test. Obter o Access Token
na seção de credenciais de teste da aplicação, nunca copiar credenciais de produção.
O valor `test` não transforma uma credencial real em sandbox: a credencial selecionada
no painel é essencial, e a verificação live_mode ocorre após a chamada externa.

No painel **Suas integrações**, selecionar a aplicação da Payments API e obter o
Access Token em **Credenciais de teste**. Para o roteiro de Webhooks em modo de
teste, a documentação exige credenciais de teste de um usuário produtivo; isso
não significa usar seu Access Token de produção.

Em **Webhooks → Configurar notificações**, preencher a URL de **modo de teste**:
`https://SEU-DOMINIO/payments/mercado-pago/webhook`. Selecionar **Pagamentos**
(tópico `payment`), salvar e revelar a assinatura secreta para preencher
`MERCADO_PAGO_WEBHOOK_SECRET`. A URL deve estar acessível via HTTPS, preservando
query e headers; não colocar autenticação JWT no proxy desse endpoint.
Usar dados de comprador de teste adequados à integração.
Não pagar QR Codes com banco real. O simulador de webhook deve referenciar um
pagamento existente no ambiente de teste, criado por esta API, com metadata e
external_reference preservados: um ID fictício recebe 409, não uma aprovação.

Esta implementação usa `/v1/payments` (Payments API). O roteiro de Pix baseado em
`/v1/orders` e eventos de Orders é uma integração diferente; não misturar seus IDs,
eventos ou passos. Consulte o [guia Pix da Payments API](https://www.mercadopago.com.br/developers/pt/docs/checkout-bricks/payment-brick/payment-submission/pix)
e [credenciais](https://www.mercadopago.com.br/developers/pt/docs/credentials).
Os mocks validam aprovação sem movimentar dinheiro. Nenhum teste externo com a
conta Mercado Pago foi executado; o comportamento disponível no sandbox da conta
precisa ser verificado antes de considerar a homologação externa concluída.

Verificações (ambiente virtual ativado): `ruff check .` e `pytest`.
Não há mypy, pyright ou outro verificador de tipagem configurado no projeto.

Revisão local em 17/09/2026: `pytest` coletou 386 testes, com **371 aprovados e
15 ignorados** por ausência de `DATABASE_URL`; dois avisos de depreciação das
dependências de testes. `ruff check .`, compilação/imports e `pip check` passaram.
Uvicorn iniciou e `GET /health` respondeu 200 com `{"status":"ok"}`.
Os testes adicionais cobrem assinatura malformada/não ASCII, request-id em branco,
pagamento local inexistente, 404/429/503/timeout do SDK e retry com aprovação única.
Também foi verificada a leitura das três configurações pelo `.env` e a precedência
da variável de ambiente para o Access Token, usando valores aleatórios efêmeros.

Nenhuma credencial real embutida foi encontrada nos arquivos revisados; `.env`
continua ignorado e `.env.example` contém somente placeholders. Esta pasta não
possui `.git`, portanto não foi possível auditar histórico ou arquivos já rastreados.
Nenhuma chamada de pagamento foi feita. A API está pronta para iniciar testes
com credenciais de teste após configurar banco, migrations e painel; a aprovação
Pix sem dinheiro foi validada com mocks. Homologação externa e persistência/locks
em PostgreSQL ainda precisam ser executados no ambiente de testes configurado.
