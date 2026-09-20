# Checkout Pro — primeiro teste ponta a ponta

O backend oferece Checkout Pro via Preferences, preservando Pix interno, autenticação
JWT e a regra central de licença. O desktop não foi alterado. Nenhuma cobrança,
configuração remota ou alteração de banco de produção é executada pelos testes.

## Configuração na Railway

Adicionar ao serviço da API:

```dotenv
API_PUBLIC_BASE_URL=https://dtf-manager-api-production.up.railway.app
```

É uma origem HTTPS, sem caminho, query, fragmento ou credenciais. Settings carrega
a variável via pydantic-settings. Sua ausência permite iniciar a API, mas checkout
retorna 503 antes de criar intenção local. Origem inválida impede a inicialização.
A preferência recebe `notification_url = API_PUBLIC_BASE_URL + /payments/mercado-pago/webhook`.

Manter `DATABASE_URL`, os secrets JWT e as três variáveis Mercado Pago existentes.
Para teste, `MERCADO_PAGO_ENVIRONMENT=test` exige pagamento consultado com
`live_mode=false`. Essa variável não transforma uma credencial real em credencial
de teste: use a credencial apropriada da conta vendedora de teste da aplicação.
Não registrar credenciais em comandos versionados ou compartilhar tokens de sessão.

No painel Mercado Pago, configurar Webhooks de **Pagamentos**, no ambiente de teste,
para `https://dtf-manager-api-production.up.railway.app/payments/mercado-pago/webhook`.
O segredo configurado na API precisa corresponder à aplicação. A configuração do
painel continua necessária: o SDK emite aviso de depreciação para `notification_url`,
que é enviado conforme o contrato solicitado, sem substituir Webhooks assinados.

Utilizar comprador e vendedor de teste distintos, do Brasil, e cartão de teste
oficial. Abrir o `init_point` em janela anônima, autenticando o comprador de teste.
Não usar conta ou cartão real. Consulte as instruções oficiais atuais:

- [Contas de teste](https://www.mercadopago.com.br/developers/pt/docs/checkout-pro-preferences/test-accounts)
- [Compras de teste](https://www.mercadopago.com.br/developers/pt/docs/checkout-pro-preferences/integration-test/test-purchases)
- [Criar preferência](https://www.mercadopago.com.br/developers/pt/reference/online-payments/checkout-pro-preferences/create-preference/post)

## Contratos e roteiro

Base: `https://dtf-manager-api-production.up.railway.app`.
Valores entre `<...>` são placeholders. Datas de resposta são UTC.

1. `GET /plans`, público, sem body. Retorna apenas planos ativos, consultados no banco:

   ```json
   [{"code":"MONTHLY","name":"<NOME_NO_BANCO>","price":"79.90","is_lifetime":false}]
   ```

   A lista contém também SEMIANNUAL, ANNUAL e LIFETIME quando ativos. Preços são strings
   com duas casas. Um plano legado sem preço retorna `price:null` e não pode ser comprado.

2. `POST /auth/register`, público, `Content-Type: application/json`:

   ```json
   {"name":"<NOME>","cpf":"<CPF_VALIDO>","email":"<EMAIL_VALIDO>","password":"<SENHA_LOCAL>","plan_code":"MONTHLY"}
   ```

   Resposta 201: `user` e `subscription`. Guardar `subscription.id`; status inicial
   PENDING, datas de ativação/vencimento nulas. CPF/email precisam ser únicos.

3. `POST /auth/login`, público, `Content-Type: application/json`:

   ```json
   {"email":"<EMAIL_DO_CADASTRO>","password":"<SENHA_LOCAL>"}
   ```

   Resposta 200: `access_token`, `refresh_token`, `token_type:"bearer"` e `expires_in`.
   Login não concede licença. Nas próximas chamadas usar `Authorization: Bearer <ACCESS_TOKEN>`.

4. `POST /payments/checkout`, Bearer e `Content-Type: application/json`:

   ```json
   {"subscription_id":"<UUID_DA_ASSINATURA>"}
   ```

   Esse é o único campo aceito. Preço, moeda, usuário, plano e referências extras são
   rejeitados (422). A assinatura precisa pertencer ao usuário e estar PENDING.
   Resposta 200:

   ```json
   {"payment_id":"<UUID_LOCAL>","status":"PENDING","amount":"79.90","currency":"BRL","init_point":"https://www.mercadopago.com.br/checkout/..."}
   ```

   Status 401: não autenticado. 404: assinatura ausente/de outro usuário. 409:
   assinatura/checkout incompatível ou criação anterior incerta. 503: configuração,
   persistência ou provedor indisponível. Respostas de pagamento usam `Cache-Control: no-store`.

5. Abrir **init_point**, não sandbox_init_point. Realizar a compra de teste.
   Não há retorno automático ao desktop nesta etapa; acompanhar pela API.

6. O Mercado Pago chama `POST /payments/mercado-pago/webhook?data.id=<ID_EXTERNO>`,
   com `x-signature` e `x-request-id`, sem JWT. Body não decide ID/status. O backend
   consulta o pagamento no Mercado Pago e aplica todas as verificações existentes:
   ID externo, UUID local em metadata, assinatura/usuário, referência, preço, BRL,
   live_mode e transição de status. HTTP 200 significa conciliação, não necessariamente
   aprovação. 401 indica assinatura inválida; 409, divergência; 503, indisponibilidade.

7. `GET /payments/<UUID_LOCAL>`, Bearer, sem body:

   ```json
   {"payment_id":"<UUID_LOCAL>","status":"APPROVED","amount":"79.90","currency":"BRL"}
   ```

   Retorna o estado persistido pelo webhook, sem nova consulta de rede. Enquanto
   aguarda pagamento/conciliação, pode retornar PENDING. IDs ausentes ou de outro
   usuário retornam o mesmo 404. Não expõe referências externas, metadata ou secrets.

8. `GET /license/status`, Bearer, sem body:

   ```json
   {"active":true,"status":"ACTIVE","plan":{"code":"MONTHLY","name":"<NOME_NO_BANCO>"},"starts_at":"<UTC_ATIVACAO>","expires_at":"<UTC_MAIS_UM_MES>","is_lifetime":false}
   ```

   Só liberar o desktop com `active=true`. Vigência contada da ativação: MONTHLY +1,
   SEMIANNUAL +6, ANNUAL +12 meses de calendário. Ajusta último dia do mês. LIFETIME
   ACTIVE usa `expires_at:null` e `is_lifetime:true`. PENDING nunca libera acesso.

No `/docs`, executar planos, cadastro e login, depois **Authorize → AccessToken**
com apenas o access token (sem prefixo Bearer). Executar checkout e consultas ali;
o pagamento ocorre no navegador. O webhook deve ser entregue pelo Mercado Pago:
Authorize não gera sua assinatura.

## Idempotência, falhas e limites

Uma intenção local usa UUID estável por assinatura/provedor, compartilhando a identidade
do Pix interno. A assinatura é bloqueada durante a preparação; existência de outro
pagamento impede criar checkout em paralelo. O Payment PENDING e marcador `creating`
são confirmados no banco antes da rede. A preferência envia exatamente `plan.price`
como número JSON Decimal, quantidade 1 e BRL, com descrição do plano. Envia:

- `external_reference = UUID da Subscription`;
- `metadata.payment_id = UUID local do Payment`.

O ID da preferência, a URL e a expiração são guardados no JSON `payments.metadata`.
O ID da preferência **não** é `provider_payment_id`: este último é preenchido apenas
com o ID de pagamento consultado no Mercado Pago.

Cliques repetidos reutilizam a mesma preferência válida por 24 horas; uma chamada
simultânea durante criação recebe 409 e pode consultar/repetir após a primeira concluir.
Não dependemos de suporte não comprovado a idempotência na criação de Preferences.
Não há retry oculto. Timeout, crash ou falha ao salvar resultado deixam a intenção
bloqueada para nova criação: reconciliar com o provedor antes de qualquer intervenção.
Não apagar a intenção nem oferecer uma nova cobrança automaticamente. Se o checkout
remoto chegou a ser pago, o webhook ainda pode conciliar usando os UUIDs persistidos.

Checkout expirado, pagamento rejeitado/cancelado, mudança do preço do plano ou troca
de fluxo exigem tratamento operacional. Renovação e nova tentativa comercial não
fazem parte desta etapa. Não mudar preços com pagamentos pendentes. Preferência
reutilizada não é garantia de que o comprador nunca tentará pagar novamente no site
externo: outro ID de pagamento para a mesma intenção é rejeitado e exige conciliação.

Somente PaymentService ativa a assinatura, atomicamente com APPROVED. Webhooks
repetidos não estendem datas. Reembolso não revoga licença automaticamente, conforme
a regra anterior. Nunca aprovar pelo retorno do navegador ou pelo body do webhook.

## Timestamp e replay

Mantida a assinatura HMAC-SHA256 do SDK, comparação constante, ID e request-id
obrigatórios. O SDK 3.5 oferece `tolerance_seconds`, mas a documentação consultada
não garante uma janela de renovação do timestamp para todas as reentregas. Há também
exemplos de timestamps em segundos/milissegundos em diferentes produtos. Não aplicar
uma janela arbitrária que possa rejeitar uma aprovação legítima atrasada.

Sem filtro temporal, replay autenticado continua possível e causa consulta ao provedor,
mas não uma segunda ativação. A proteção financeira permanece consulta atual +
reconciliação + idempotência. Os testes preservam esse comportamento explicitamente.
[Referência oficial de Webhooks](https://www.mercadopago.com.br/developers/pt/docs/links-and-debts/additional-content/your-integrations/notifications/webhooks?scope=prod).

## Publicação e verificação

Não foi criada migration; HEAD continua `0002_plan_price`. No shell do serviço Railway,
após publicar a versão e configurar a nova variável, verificar:

```sh
python -m alembic current
```

Se o banco já está em `0002_plan_price` e os planos já possuem os preços oficiais,
nenhum comando de alteração de banco é necessário. Apenas para banco ainda não preparado:

```sh
python -m alembic upgrade head
python -m scripts.seed_plans
```

Comando de inicialização permanece:

```sh
python -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
```

Validação local: `pytest`, `ruff check .` e `python -m compileall -q app scripts tests`.
Executar testes com configurações isoladas e PostgreSQL descartável; testes legados
com DATABASE_URL configurada podem escrever registros temporários, portanto nunca
apontá-los à produção. Sem banco de testes, os testes PostgreSQL são marcados como skipped.
Chamadas Mercado Pago são simuladas e a rede bloqueada nos testes de integração do provider.

Verificação local desta implementação: 420 testes aprovados e 15 testes PostgreSQL
ignorados por ausência de banco descartável configurado; Ruff, compilação e startup
Uvicorn com `/health` 200 aprovados. Três avisos de depreciação (Starlette/httpx,
AnyIO e notification_url do SDK), sem falhas. Não há verificador estático de tipagem
configurado no pyproject. O teste com conta Mercado Pago/Railway ainda deve ser
executado pelo responsável após publicação e configuração.
