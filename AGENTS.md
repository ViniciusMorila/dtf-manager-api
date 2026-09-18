# Instruções permanentes — API DTF Manager

Estas instruções se aplicam a todo este repositório e às futuras implementações da API oficial do DTF Manager. Preserve estas regras ao criar ou alterar código, testes, migrações e documentação.

## Escopo da etapa atual

- ETAPA 1 autorizada: estrutura modular, dependências em `pyproject.toml` para Python >=3.12 e `GET /health` retornando `{"status": "ok"}`.
- ETAPA 2 autorizada: centralizar as oito variáveis de ambiente em `app/core/config.py` com pydantic-settings, leitura de `.env`, validação de secrets em production e testes de configuração.
- ETAPA 3 autorizada: configurar PostgreSQL com SQLAlchemy 2.x, psycopg 3, sessões por dependency injection e Alembic. Não criar models de domínio nem migration inicial antes de implementar os models.
- ETAPA 4 autorizada: implementar User, Plan, Subscription, Payment e RefreshToken, criar e aplicar a migration inicial com Alembic e validar o schema PostgreSQL.
- ETAPA 5 autorizada: criar o seed idempotente dos quatro planos padrão em `scripts/seed_plans.py` e documentar sua execução. Usar `code` como identificador único e inserir apenas planos ausentes, preservando os existentes.
- ETAPA 6 autorizada: schemas Pydantic 2 de entrada e saída para User, Plan, Subscription, Authentication e License, sem novos endpoints. Usar ConfigDict, EmailStr e from_attributes nas projeções ORM apropriadas.
- ETAPA 7 autorizada: hash e verificação de senha com argon2-cffi em `app/shared/security/password.py`, validação mínima de 8 caracteres com letra e número e testes unitários. Nunca registrar senhas nos logs nem implementar algoritmo criptográfico próprio.
- ETAPA 8 autorizada: utilitário `app/shared/utils/cpf.py` com normalização e validação dos dois dígitos verificadores, rejeição de caracteres inválidos e números repetidos, acompanhado de testes. Nunca usar CPF para gerar licenças ou tokens.
- ETAPA 9 autorizada: `POST /auth/register`, com validação Pydantic, email lowercase, CPF validado/normalizado, plano ativo, Argon2 e criação atômica de usuário e assinatura PENDING. Não emitir JWT nem licença ativa. Tratar duplicidades inclusive por constraints únicas, e não devolver valores de entrada em erros de validação.
- ETAPA 10 autorizada: serviço JWT isolado em `app/shared/security/jwt.py` com PyJWT, criação/decodificação de access e refresh, secrets distintos e tempos configuráveis. Payload somente sub, type, jti, iat e exp. Não integrar emissão ao cadastro nem adicionar endpoints nesta etapa.
- ETAPA 11 autorizada: `POST /auth/login`, normalização de email, verificação da conta ativa e Argon2, emissão de access/refresh e persistência somente do hash do refresh. Credenciais inválidas devem produzir resposta genérica, sem revelar existência da conta. Login não valida nem ativa licença.
- ETAPA 12 autorizada: `POST /auth/refresh` com rotação atômica, validação de JWT/conta/registro, bloqueio de linha, revogação do token usado e persistência somente do hash do novo refresh. Tokens utilizados não podem ser reutilizados; falhas devem reverter a transação inteira.
- ETAPA 13 autorizada: `POST /auth/logout`, revogação idempotente pelo hash do refresh token recebido, definindo revoked_at somente quando nulo. Nunca retornar tokens armazenados e não revogar outras sessões.
- ETAPA 14 autorizada: `SubscriptionService.get_license_status(user_id)` como lógica central de autorização de licença. Consultar banco e relógio da API em UTC, nunca relógio/claims do cliente. Persistir ACTIVE comum vencida como EXPIRED; planos vitalícios exigem expires_at nulo, assinatura ACTIVE e conta ativa. Nenhum endpoint novo nesta etapa.
- ETAPA 15 autorizada: `SubscriptionService.activate_subscription(subscription_id)`, exclusivamente interno, com datas UTC e meses de calendário (1/6/12 ou vitalício). Ativação inicial idempotente com lock e transação; repetição não estende datas nem reativa estados posteriores. O chamador deverá ter confirmado o pagamento.
- ETAPA 16 autorizada: dependency Bearer com access token validado e conta ativa, `GET /license/status` e `GET /me`. Identidade exclusivamente do sub do JWT, nunca de body/query. Ambos usam SubscriptionService para dados da assinatura. /me retorna somente id, name, email e resumo público da assinatura.
- ETAPA 17 autorizada: PaymentProvider abstrato e PaymentService para criar/atualizar pagamentos e processar aprovação idempotente. Não chamar Mercado Pago nem expor endpoints. APPROVED e activate_subscription devem participar da mesma transação; repetir aprovação não ativa nem estende de novo. Usar Decimal e identidade externa única por provedor quando disponível.
- Schemas públicos devem declarar campos explicitamente e nunca incluir password_hash, token_hash, secrets JWT ou relacionamentos internos. Credenciais de entrada devem usar SecretStr e ser excluídas da serialização. Tokens de sessão só são entregues pela resposta explícita de autenticação.
- `.env` não deve ser versionado; `.env.example` deve conter apenas exemplos fictícios. Não definir secrets padrão no código. Secrets JWT ausentes ou inseguros devem impedir a inicialização em production.
- Adicionar tipagem Python em todo código novo.
- Validar imports, executar testes básicos e verificar a inicialização com Uvicorn.
- Endpoints autorizados: health, cadastro, login, refresh, logout, /license/status, /me e POST /payments/mercado-pago/webhook. Criação de cobrança e ativação continuam internas.
- NÃO alterar o aplicativo DTF Manager.
- ETAPA 18 autorizada: SDK oficial Mercado Pago isolado no provider, cobrança Pix interna, preço Decimal no plano e webhook público assinado. Consultar o pagamento no provedor antes de reconciliar; validar ID, referência, vínculos, valor, moeda e ambiente. Somente PaymentService ativa a assinatura. Persistir intenção antes da rede e reutilizar chave/corpo no retry. Testar com mocks, nunca fazer cobranças reais.
- Funcionalidades além da etapa 18 dependem de uma solicitação posterior do usuário.

## Arquitetura obrigatória

```text
DTF MANAGER (aplicativo)
          ↓ HTTPS
    API DTF MANAGER
          ↓
      POSTGRESQL
```

- O aplicativo NUNCA poderá acessar diretamente o PostgreSQL.
- Toda autenticação, cadastro, licença, plano, pagamento e validação de acesso deverá passar pela API.
- Somente o backend acessa o banco. Não fornecer credenciais de banco ao aplicativo.
- A comunicação do aplicativo com a API deverá utilizar HTTPS.

## Linguagem e stack

- Utilizar exclusivamente Python 3.12 ou superior no backend.
- Utilizar FastAPI, Uvicorn, SQLAlchemy 2.x, Alembic e PostgreSQL.
- Utilizar psycopg 3 como driver PostgreSQL.
- Utilizar Pydantic 2 e pydantic-settings.
- Utilizar PyJWT para JWT e Argon2 para hash de senhas.
- Utilizar pytest e httpx nos testes futuros.

## Modularidade

- Separar configuração, segurança e persistência dos módulos de domínio.
- Organizar autenticação, usuários/contas, planos, assinaturas/licenças e pagamentos em módulos com responsabilidades claras.
- Separar rotas HTTP, contratos de validação, regras de negócio e acesso a dados.
- Gerenciar alterações de esquema com Alembic.
- Nunca substituir migrations por `create_all()`. Models devem herdar de `app.db.base.Base`, usar `Mapped` e `mapped_column` e ficar em `app/modules/<domínio>/models.py` ou no pacote `models/`, descobertos pelo Alembic.
- Usar UUID nas chaves primárias, ForeignKey, relationships, índices e datas com timezone UTC. Dinheiro deve usar Decimal/Numeric, nunca float. Persistir somente password_hash e token_hash, nunca senha ou refresh token puros.
- CPF, email e código de plano são únicos. Pagamentos externos são únicos por `(provider, provider_payment_id)`; identificadores nulos são permitidos.
- Usar `SessionDependency` nas rotas que precisarem do banco. A sessão é fechada ao final da requisição e sofre rollback em exceções; o serviço decide explicitamente quando fazer commit.
- Manter a futura integração com Mercado Pago no domínio de pagamentos.

## Cadastro e planos

- O cadastro recebe nome, CPF, email, senha e plano escolhido.
- Preservar os identificadores e durações dos planos:
  - `MONTHLY`: 1 mês.
  - `SEMIANNUAL`: 6 meses.
  - `ANNUAL`: 1 ano.
  - `LIFETIME`: vitalício.
- Escolher um plano NÃO significa possuir licença ativa.
- Ao cadastrar, criar `Subscription` com status `PENDING`.
- Somente após pagamento confirmado, tornar a assinatura `ACTIVE`.

## Assinaturas e validação de licença

- A assinatura deverá possuir plano, status, data de início (`starts_at`), data de vencimento (`expires_at`) e usuário associado.
- Para `LIFETIME`, `expires_at` deverá ser `null`.
- Uma assinatura vitalícia também depende de pagamento confirmado e status válido para conceder acesso.
- Sempre consultar a validade da licença no banco através da API, considerando status e vigência.
- Toda futura funcionalidade protegida por licença deve usar `SubscriptionService.get_license_status` e exigir `active = true`, sem duplicar regras. Falha do serviço nunca concede autorização. Login/refresh continuam apenas autenticando.
- Não conceder acesso licenciado a assinaturas `PENDING` ou vencidas.
- Nunca gerar licença a partir de CPF, email ou nome.
- Não usar dados fornecidos pelo aplicativo como prova de pagamento ou de licença ativa.

## Autenticação e secrets

- Nunca armazenar senha em texto puro; utilizar hash Argon2.
- Nunca colocar secrets no código, nos testes ou em arquivos versionados.
- Carregar configurações sensíveis de fontes externas por pydantic-settings.
- Configurar JWT de acesso com 15 minutos e refresh de sessão com 30 dias por padrão. O modelo está em `.env.example` e Settings carrega `.env`; o login emite esses tokens e persiste apenas o digest do refresh.
- JWT representa autenticação e NÃO a duração da licença.
- Não usar validade ou conteúdo do JWT como substituto da consulta da licença no banco pela API.

## Evolução prevista

- Mercado Pago utiliza SDK oficial no provider, secrets exclusivamente externos e testes sem rede. Não adicionar SDK ao restante do domínio.
- A confirmação de pagamento deverá ser validada pela API antes de ativar a assinatura.
- O aplicativo posteriormente utilizará a API para login, refresh de sessão, logout, verificação da licença e dados da conta.
- Os contratos e mecanismos ainda não especificados deverão ser definidos na etapa de implementação, respeitando estas regras.
- Manter `README.md` e estas instruções coerentes com as decisões autorizadas pelo usuário.
