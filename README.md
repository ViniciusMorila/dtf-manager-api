# API DTF Manager

Etapa 18: integração inicial Mercado Pago pelo SDK oficial, cobrança Pix interna e
`POST /payments/mercado-pago/webhook`. Consulte [configuração, preços e operação](docs/mercado_pago.md).

Revisão de testes da etapa 18: assinatura inválida retorna 401, pagamento sem
intenção local retorna 409 e falha temporária do provedor retorna 503 para retry.
Conciliação e ativação continuam no PaymentService. O roteiro de configuração e
as limitações da validação local estão documentados no guia acima.

API oficial do DTF Manager. O projeto fornece configurações centralizadas, infraestrutura PostgreSQL/Alembic, models, seed de planos, schemas públicos, utilitários de segurança e serviços de licença e pagamentos. Além do webhook, os endpoints disponíveis são `GET /health`, `POST /auth/register`, `POST /auth/login`, `POST /auth/refresh`, `POST /auth/logout`, `GET /license/status` e `GET /me`. O aplicativo DTF Manager não foi alterado.

## Arquitetura obrigatória

```text
DTF MANAGER (aplicativo)
          ↓ HTTPS
    API DTF MANAGER
          ↓
      POSTGRESQL
```

O aplicativo DTF Manager NUNCA poderá acessar diretamente o PostgreSQL. Toda autenticação, cadastro, licença, plano, pagamento e validação de acesso deverá passar pela API. O acesso ao banco é responsabilidade exclusiva do backend.

## Stack obrigatória

O backend utilizará exclusivamente Python 3.12 ou superior, com:

- FastAPI: API HTTP.
- Uvicorn: servidor ASGI.
- SQLAlchemy 2.x: persistência e mapeamento de dados.
- Alembic: migrações de banco de dados.
- PostgreSQL: banco de dados.
- psycopg 3: driver PostgreSQL.
- Pydantic 2: validação de dados e contratos da API.
- pydantic-settings: configuração externa à aplicação.
- PyJWT: tokens de autenticação.
- Argon2: hash de senhas.
- pytest: testes.
- httpx: cliente HTTP e testes da API.

## Organização modular prevista

A implementação futura deverá separar configuração, segurança, persistência e os domínios de autenticação, usuários/contas, planos, assinaturas/licenças e pagamentos. Cada módulo deverá manter responsabilidades claras entre rotas HTTP, contratos de entrada/saída, regras de negócio e acesso a dados.

As migrações são gerenciadas pelo Alembic. Os models estão nos módulos users, plans, subscriptions, payments e auth. O cadastro está no módulo auth, separado em rota, serviço transacional e schemas. A integração futura com Mercado Pago deverá ficar no domínio de pagamentos.

```text
app/
  main.py
  core/                 # Configuração e health
  db/                   # Base declarativa e sessões PostgreSQL
  modules/
    auth/
    users/
    plans/
    subscriptions/
    payments/
    licenses/
  shared/
    exceptions/
    security/
    utils/
alembic/
  env.py
  script.py.mako
  versions/             # 0001_initial_schema.py
alembic.ini
tests/
pyproject.toml
```

## Execução local

Com Python 3.12 ou superior, execute na raiz do projeto (PowerShell):

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Consulte `http://127.0.0.1:8000/health`. A resposta HTTP 200 será:

```json
{"status": "ok"}
```

O HTTP em loopback serve apenas ao desenvolvimento local. A comunicação do aplicativo com a API implantada deve ocorrer por HTTPS, configurado na infraestrutura de implantação.

O endpoint verifica apenas se a API responde; não verifica PostgreSQL nem serviços externos. Em development, não é necessário banco ou secret para iniciar. A variável de ambiente opcional `DTF_APP_NAME` continua configurando o título da API por pydantic-settings.

## Deploy na Railway

Conecte o repositório GitHub ao serviço Railway e use a raiz do repositório como
diretório do serviço. Selecione o builder **Railpack**, que detecta Python pelo
`pyproject.toml`. O arquivo `.python-version` seleciona Python 3.12; o projeto
continua aceitando Python >=3.12. Não é necessário Dockerfile. O `railway.toml`
agora versiona builder, build, start e healthcheck, eliminando a dependência de
configuração manual desses comandos. Use o arquivo `/railway.toml` da raiz.

O `requirements.txt` aciona a instalação pip do Railpack no ambiente virtual que
é incluído na imagem final. Ele espelha apenas `project.dependencies` do
`pyproject.toml`; o teste `tests/test_deployment.py` impede divergências. Ao mudar
dependências de produção, atualize os dois arquivos. FastAPI e Uvicorn pertencem
às dependências de produção, assim como o SDK Mercado Pago, simplejson e requests.
`python-dateutil` não é utilizado: os cálculos de calendário usam a biblioteca
padrão. O pydantic-core e email-validator são instalados por `pydantic[email]`.

O **Build Command** usa explicitamente o ambiente virtual preservado pelo Railpack:

```sh
/app/.venv/bin/python -m pip install . && /app/.venv/bin/python -m pip check && /app/.venv/bin/python -m uvicorn --version
```

Esse comando instala o projeto e as dependências de produção declaradas no
`pyproject.toml`, sem o extra `dev`. O **Start Command** também está versionado,
pois a entrada deste projeto é `app.main:app`:

```sh
/app/.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

O Railpack executa o comando em shell, expandindo `PORT`, fornecida pela Railway.
O arquivo também define `restartPolicyType = "ON_FAILURE"`.
Não fixe `PORT=8000` nas variáveis do serviço e não use `--reload` em produção.
Defina **Healthcheck Path** como `/health`. Em **Networking**, gere um domínio
público HTTPS e verifique `https://SEU-DOMINIO/health`: deve responder HTTP 200
com `{"status":"ok"}`. Esse healthcheck verifica o processo, não a conexão ao banco.

Em **Variables**, forneça externamente a configuração existente:

- `ENVIRONMENT=production`.
- `JWT_ACCESS_SECRET` e `JWT_REFRESH_SECRET`: valores aleatórios, distintos e
  seguros conforme a validação descrita abaixo. Não copie os placeholders.
- `DATABASE_URL`: URL do PostgreSQL acessível pelo backend, com esquema
  `postgresql+psycopg://`. A URL padrão `postgresql://` deve ter apenas o esquema
  adaptado para psycopg 3, preservando os demais componentes e parâmetros.
- As variáveis Mercado Pago, se a integração for habilitada, conforme
  [o guia existente](docs/mercado_pago.md). Configuração parcial ou fictícia
  impede a inicialização em produção. Se desabilitada, omita as três variáveis.

Não envie `.env` nem copie secrets para arquivos versionados. As migrations
existentes precisam estar aplicadas no banco para os endpoints de negócio;
consulte a seção de Alembic deste README. O comando de start não executa migrations
nem seed, e esta preparação não altera ou acessa o banco.

Após enviar essas alterações ao GitHub, faça o deploy, confira os logs de build
e inicialização e valide `/health` no domínio gerado. A verificação local não
substitui a confirmação do build e das variáveis no ambiente Railway.

Se ocorrer `No module named uvicorn`, o Python do start não está encontrando as
dependências de produção. FastAPI e Uvicorn já estavam declarados anteriormente;
apenas ter `pyproject.toml` não garantia a instalação automática pip no fluxo
anterior, que não tinha `requirements.txt` nem um comando de build versionado.
Um build bem-sucedido não garante que o Python selecionado no start encontre os
mesmos pacotes. O provider Python do Railpack cria `/app/.venv` na instalação pip
e inclui esse diretório na imagem final. Modificações em outros diretórios do
Python de build não têm essa mesma garantia de preservação. Build e start agora
invocam `/app/.venv/bin/python` explicitamente, sem depender da ordem do PATH.
O build verifica Uvicorn nesse mesmo ambiente antes de criar o deployment.
Não há instalação de pacotes no start. Essa configuração depende do provider
Python/pip do Railpack, acionado pelo `requirements.txt` presente na raiz.

Se o erro persistir após um push, confira no deploy afetado o commit utilizado,
o serviço/repositório e a branch, o Root Directory (raiz deste projeto) e o
Railway Config File (`/railway.toml`). Confira a configuração efetiva daquele
deploy, inclusive possíveis overrides por ambiente, e os logs completos de build.
O caminho `/mise/installs/python/3.12/bin/python` no erro, por si só, não comprova
qual dessas configurações falhou nem que o último commit foi implantado.

Referências: [provider Python do Railpack](https://github.com/railwayapp/railpack/blob/main/core/providers/python/python.go),
[Python no Railpack](https://railpack.com/languages/python) e
[Start Command na Railway](https://docs.railway.com/deployments/start-command).

## Configuração — etapa 2

`Settings`, em `app/core/config.py`, carrega as variáveis do ambiente e o arquivo `.env` em UTF-8 no diretório de execução. Variáveis do processo têm prioridade sobre o arquivo. Para preparar a configuração local, copie `.env.example` para `.env` se este ainda não existir. `.env` é ignorado pelo Git; o exemplo contém apenas valores fictícios.

| Variável | Padrão / validação |
| --- | --- |
| `DATABASE_URL` | Ausente por padrão; armazenada como `SecretStr`, sem abrir conexão |
| `JWT_ACCESS_SECRET` | Ausente por padrão; `SecretStr` |
| `JWT_REFRESH_SECRET` | Ausente por padrão; `SecretStr` |
| `JWT_ACCESS_EXPIRE_MINUTES` | 15; inteiro positivo |
| `JWT_REFRESH_EXPIRE_DAYS` | 30; inteiro positivo |
| `ENVIRONMENT` | `development`; aceita também `testing`, `staging` e `production` |
| `HOST` | `127.0.0.1`; texto não vazio |
| `PORT` | 8000; inteiro entre 1 e 65535 |

Ao criar a aplicação, Settings é validada antes de disponibilizar as rotas. Em `production`, ambos os secrets JWT são obrigatórios, distintos e devem possuir pelo menos 32 caracteres, com ao menos 8 caracteres diferentes, sem espaços nem marcadores comuns como `trocar`, `example` ou `secret`. Valores ausentes ou inseguros impedem a inicialização. Essas verificações não comprovam aleatoriedade: forneça dois valores independentes gerados criptograficamente, por exemplo com `secrets.token_urlsafe(32)`, e mantenha-os fora dos arquivos versionados.

Secrets e URL do banco são mascarados nas representações e na serialização padrão de Settings; as mensagens de validação ocultam os valores de entrada. Não registre valores obtidos com `get_secret_value()` nem detalhes brutos de erros de validação.

`HOST` e `PORT` ficam disponíveis em Settings; o comando direto `uvicorn app.main:app` continua usando suas próprias opções `--host` e `--port`. O serviço JWT usa os parâmetros de expiração e o login emite os tokens; nenhum endpoint valida licenças nesta etapa.

Execute os testes básicos:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

As dependências de execução estão no `pyproject.toml`; pytest e httpx estão no extra `dev`. O servidor PostgreSQL deve ser fornecido externamente; ele não é instalado por este projeto.

## PostgreSQL e migrations — etapa 3

`app/db/base.py` define `Base` com `DeclarativeBase` e convenções de nomes para constraints e índices. Models futuros deverão herdar dessa base e declarar atributos com `Mapped` e `mapped_column`. Coloque-os em `app/modules/<domínio>/models.py` ou em arquivos dentro do pacote `models/`. O Alembic importa esses módulos antes de usar `Base.metadata` como `target_metadata`, permitindo autogenerate. Check constraints devem receber nomes explícitos para a convenção adotada.

`app/db/session.py` fornece um engine síncrono por processo, com psycopg 3, `pool_pre_ping`, timeout de conexão de 5 segundos e parâmetros SQL ocultos nos erros. O engine e as conexões são criados sob demanda. `DATABASE_URL` é obrigatória ao acessar o banco ou executar o ambiente Alembic e deve usar `postgresql+psycopg://` com o nome do banco. Nenhuma credencial é definida no código ou no `alembic.ini`.

Nas rotas futuras, declare `session: SessionDependency`, importando o alias de `app.db.session`. O FastAPI injeta uma sessão própria por requisição. A sessão é sempre fechada e exceções provocam rollback. Não há commit automático: os serviços deverão controlar explicitamente suas transações. Operações com essa sessão síncrona devem ser executadas em rotas `def`, para não bloquear o event loop.

Configure uma URL real no `.env` local ou no ambiente e execute o teste de conexão, que faz somente `SELECT 1`:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_db.py::test_postgresql_connection -q
```

Sem `DATABASE_URL`, esse teste é marcado como ignorado; com a variável definida, falhas de conexão fazem o teste falhar. O `/health` continua verificando apenas a disponibilidade da API e não consulta o banco.

O schema é controlado exclusivamente pelo Alembic; não há chamada a `create_all()`. A revisão inicial é `0001_initial_schema`. Para inspecionar o histórico atual:

```powershell
.\.venv\Scripts\python.exe -m alembic heads
.\.venv\Scripts\python.exe -m alembic history
```

Com `DATABASE_URL` real configurada e o banco PostgreSQL acessível, aplique a migration já criada e valide o schema:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m pytest tests/test_models.py::test_migrated_postgresql_schema -q
.\.venv\Scripts\python.exe -m alembic check
```

O Alembic usa Settings diretamente, aceita execução online e geração SQL offline (`upgrade head --sql`) e fecha sua conexão ao terminar. Não executamos migrations na inicialização da API.

## Models — etapa 4

| Model | Tabela | Particularidades |
| --- | --- | --- |
| `User` | `users` | CPF de 11 dígitos e email únicos; somente `password_hash` |
| `Plan` | `plans` | Código enum único; constraint associa código à duração de 1, 6 ou 12 meses, ou vitalício com duração nula |
| `Subscription` | `subscriptions` | FKs para usuário e plano; status inicial `PENDING`; datas de vigência e ativação/cancelamento opcionais |
| `Payment` | `payments` | FKs para usuário e assinatura; `Numeric(18, 2)`/`Decimal`; valor não negativo; JSON opcional |
| `RefreshToken` | `refresh_tokens` | FK para usuário; somente `token_hash` único; expiração obrigatória e revogação opcional |

Todos usam UUID como chave primária, gerado com `uuid4` pelo ORM. Relacionamentos são bidirecionais. FKs possuem índices, e constraints únicas já fornecem seus próprios índices no PostgreSQL. Há índices de expiração e de `(user_id, status)` para assinaturas. Nenhuma exclusão em cascata de histórico financeiro foi configurada.

`SubscriptionStatus`: `PENDING`, `ACTIVE`, `EXPIRED`, `CANCELLED`, `SUSPENDED`. `PaymentStatus`: `PENDING`, `APPROVED`, `REJECTED`, `CANCELLED`, `REFUNDED`. Ambos são enums nativos PostgreSQL, assim como `PlanCode`. A migration cria e remove esses tipos explicitamente através das operações Alembic/SQLAlchemy.

Pagamentos são únicos por `(provider, provider_payment_id)`: o mesmo ID no mesmo provedor é rejeitado; IDs iguais em provedores diferentes são permitidos. Vários registros podem ter ID externo nulo. A coluna SQL `metadata` é acessada no Python por `Payment.payment_metadata`, pois `metadata` é um nome reservado do SQLAlchemy.

Datas usam `TIMESTAMP WITH TIME ZONE`; as conexões da API e do Alembic usam UTC. `created_at` e `updated_at` recebem `now()` no banco. O ORM atualiza `updated_at` ao emitir updates; SQL externo deverá atualizar esse campo explicitamente. Serviços futuros deverão fornecer datas com timezone e valores monetários `Decimal`, validar/normalizar CPF e email e calcular os hashes antes de persistir. Não há geração de senha, token ou licença nesta etapa.

Nenhum plano ou outro dado é inserido pela migration. Regras de ativação após pagamento, consistência da vigência vitalícia e correspondência entre usuário do pagamento e da assinatura serão tratadas nos serviços de negócio futuros; as FKs atuais garantem a existência das referências.

Validação local da etapa 4: models, relacionamentos e SQL de upgrade/downgrade foram testados. A tentativa de `alembic upgrade head` foi bloqueada por ausência de `DATABASE_URL`; a aplicação da migration e a existência das tabelas no PostgreSQL ainda precisam ser confirmadas. O teste de schema faz inspeção read-only e compara o banco com os metadados, incluindo tipos e defaults, após verificar a revisão Alembic.

## Seed dos planos — etapa 5

Com as dependências instaladas, `DATABASE_URL` real configurada no ambiente ou `.env` local e as migrations aplicadas, execute na raiz do projeto:

```powershell
.\.venv\Scripts\python.exe -m scripts.seed_plans
```

O script `scripts/seed_plans.py` insere os planos abaixo, inicialmente com `is_active = true`:

| code | name | duration_months | is_lifetime |
| --- | --- | --- | --- |
| `MONTHLY` | 1 mês | 1 | false |
| `SEMIANNUAL` | 6 meses | 6 | false |
| `ANNUAL` | 1 ano | 12 | false |
| `LIFETIME` | Vitalício | null | true |

O seed usa `INSERT ... ON CONFLICT (code) DO NOTHING`, apoiado pela constraint única do banco. Execuções repetidas ou concorrentes não duplicam planos. Registros existentes são preservados integralmente, inclusive UUID, nome, datas e `is_active`; um plano desativado não é reativado pelo seed. Se faltar apenas parte do catálogo, somente os códigos ausentes são inseridos.

As inserções são confirmadas em uma única transação; falhas provocam rollback e código de saída 1. O comando informa a quantidade inserida após o commit. O seed não cria tabelas, não aplica migrations e não roda automaticamente na inicialização da API. Cadastrar planos não cria assinaturas ou licenças.

Para executar os testes do seed:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_seed_plans.py -q
```

O teste PostgreSQL executa o seed três vezes na mesma transação, verifica os dados e faz rollback ao terminar. Sem `DATABASE_URL`, esse teste é ignorado; os testes de catálogo, SQL e erro de configuração continuam executáveis.

## Schemas Pydantic — etapa 6

Os contratos ficam em `app/modules/<domínio>/schemas/input.py` e `output.py`, separados dos models SQLAlchemy. A resposta composta do cadastro está em `app/modules/auth/schemas/registration.py`.

| Domínio | Entrada | Saída |
| --- | --- | --- |
| User | `UserCreate`: nome, CPF, email, senha e código de plano | `UserRead`: id, nome, email, estado da conta e datas |
| Plan | `PlanSelection`: código do plano | `PlanRead`: id, código, nome, duração, vitalício e estado |
| Subscription | `SubscriptionCreate`: código do plano | `SubscriptionRead`: id, plano, status e datas |
| Authentication | `LoginRequest`, `RefreshRequest`, `LogoutRequest` | `TokenResponse`, `LogoutResponse` |
| License | `LicenseCheckRequest`: contrato vazio, rejeita claims do cliente | `LicenseRead`: resultado, plano, status, vencimento e instante da consulta |

As bases em `app/shared/schemas.py` usam `ConfigDict`. Entradas rejeitam campos extras; as projeções ORM usam `from_attributes=True` e serializam somente os campos declarados, sem relacionamentos automáticos. CPF não faz parte da saída pública de usuário. `password_hash`, `token_hash`, secrets JWT e metadados internos não fazem parte dos contratos públicos.

Email usa `EmailStr`; `pydantic[email]` instala seu suporte de validação. No cadastro, o email é convertido para lowercase; o nome é aparado e deve ser não vazio; o CPF aceita formatação, é validado pelos dois dígitos e normalizado para 11 números. `UserCreate` aplica a política mínima de senha, também verificada por `hash_password`. Entradas com senha/refresh token usam `SecretStr`, `repr=False` e `exclude=True`: o serviço acessa explicitamente `get_secret_value()`, sem registrar o valor. `model_dump()` dessas entradas não fornece as credenciais.

`TokenResponse` entrega deliberadamente os tokens de acesso e refresh ao titular da sessão, com `token_type=bearer` e `expires_in` em segundos. Esses tokens não são secrets de assinatura JWT e não são anexados aos dados de usuário. Não registrar essa resposta. Nenhum schema gera tokens ou realiza autenticação.

Datas públicas exigem timezone e são normalizadas para UTC. O contrato inicial `LicenseRead` não é usado pelo endpoint atual: `GET /license/status` retorna `LicenseStatus`, calculado pelo serviço após consulta ao banco. A decisão nunca é inferida da duração do JWT ou apenas de um vencimento nulo. O endpoint GET não possui corpo ou parâmetros de identidade.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_schemas.py -q
```

## Senhas — etapa 7

`app/shared/security/password.py` fornece:

- `validate_password_strength(password: str) -> None`: exige pelo menos 8 caracteres, uma letra e um dígito decimal. Rejeita senhas fracas com `ValueError` e mensagem genérica, sem incluir a senha.
- `hash_password(password: str) -> str`: aplica a política e utiliza `argon2-cffi.PasswordHasher` com Argon2id e salt aleatório gerenciado pela biblioteca. O resultado codificado inclui salt e parâmetros; somente esse hash deverá ser persistido em `password_hash`.
- `verify_password(password: str, password_hash: str) -> bool`: verifica com a biblioteca e retorna `False` para senha incorreta ou hash inválido. Não reaplica a política de força, para permitir a verificação de credenciais existentes.

Não são exigidas maiúsculas, símbolos ou combinações adicionais. Letras Unicode e espaços são permitidos; a senha não é aparada, normalizada nem convertida para minúsculas. Os parâmetros de custo são os padrões da biblioteca instalada. As funções não registram senhas ou hashes em logs. Nenhum endpoint, fluxo de cadastro ou login foi implementado nesta etapa.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_password.py -q
```

## CPF — etapa 8

`app/shared/utils/cpf.py` fornece:

- `normalize_cpf(cpf: str) -> str`: remove pontos, hífens e espaços (incluindo tabulações e quebras de linha), preserva zeros à esquerda e retorna somente dígitos ASCII. Outros caracteres provocam `ValueError`; letras e símbolos não são descartados silenciosamente.
- `validate_cpf(cpf: str) -> bool`: aceita CPF formatado ou sem formatação, exige 11 dígitos, rejeita sequências com todos os números iguais e verifica os dois dígitos verificadores. Retorna `False` para comprimento, caracteres ou verificadores inválidos.

A normalização sozinha não confirma a validade do CPF. O cadastro da etapa 9 valida e persiste a string normalizada, nunca um inteiro. A validação é matemática e não consulta situação cadastral nem confirma identidade. CPF não é utilizado para gerar licença ou tokens.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_cpf.py -q
```

## Cadastro HTTP — etapa 9

Pré-requisitos: PostgreSQL acessível, `DATABASE_URL` configurada, `alembic upgrade head` aplicado e seed de planos executado. O health continua disponível sem consultar o banco.

`POST /auth/register` recebe JSON:

```json
{
  "name": "Nome do usuário",
  "cpf": "123.456.789-09",
  "email": "usuario@example.com",
  "password": "Exemplo123",
  "plan_code": "MONTHLY"
}
```

Os dados acima são fictícios. A resposta HTTP **201 Created** contém apenas `user` (`UserRead`) e `subscription` (`SubscriptionRead`). A assinatura é criada com `status = PENDING` e `starts_at`, `expires_at`, `activated_at` e `cancelled_at` nulos, inclusive no plano vitalício. Não há JWT, refresh token, senha, password_hash ou licença ativa na resposta.

O serviço `app/modules/auth/registration.py` verifica email (sem diferenciar maiúsculas/minúsculas), CPF e plano ativo, calcula Argon2id e insere usuário e assinatura em uma única transação `Session.begin()`. Há flush do usuário antes da assinatura, mas somente um commit ao final. Falhas de inserção da assinatura ou de commit revertem toda a transação. O plano é lido com lock compartilhado durante a transação, impedindo alteração concorrente enquanto o cadastro está em andamento.

As verificações prévias de duplicidade são complementadas pelas constraints únicas do PostgreSQL, com tradução de conflitos concorrentes. Os erros usam o formato `{"error": {"code": "...", "message": "..."}}`:

| HTTP | code | Situação |
| --- | --- | --- |
| 409 | `EMAIL_ALREADY_EXISTS` | Email já cadastrado |
| 409 | `CPF_ALREADY_EXISTS` | CPF já cadastrado |
| 422 | `INVALID_CPF` | CPF inválido |
| 422 | `INVALID_PLAN` | Código desconhecido, plano ausente ou inativo |
| 422 | `WEAK_PASSWORD` | Senha fora da política mínima |
| 422 | `VALIDATION_ERROR` | Nome/email inválido, campos extras ou outros erros de entrada |
| 503 | `REGISTRATION_UNAVAILABLE` | Falha operacional do banco durante o cadastro |
| 500 | `REGISTRATION_FAILED` | Outra falha de persistência |

As respostas de erro não ecoam o JSON recebido, senha, CPF, mensagens SQL ou detalhes internos. A API não registra credenciais.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_register.py -q
```

Os testes HTTP isolados cobrem sucesso, normalização, Argon2, dados inválidos, duplicidades, plano inativo, conflitos de constraints e falha na assinatura. Os testes PostgreSQL exercitam persistência e rollback real usando savepoints e revertendo os dados de teste ao terminar; sem `DATABASE_URL`, são ignorados.

## Serviço JWT — etapa 10

`app/shared/security/jwt.py` expõe quatro funções isoladas:

- `create_access_token(user_id: UUID, *, settings: Settings | None = None) -> str`.
- `create_refresh_token(user_id: UUID, *, settings: Settings | None = None) -> str`.
- `decode_access_token(token: str, *, settings: Settings | None = None) -> TokenPayload`.
- `decode_refresh_token(token: str, *, settings: Settings | None = None) -> TokenPayload`.

Sem o argumento opcional, as funções carregam `Settings` do ambiente/`.env`. Access usa `JWT_ACCESS_SECRET` e `JWT_ACCESS_EXPIRE_MINUTES`; refresh usa `JWT_REFRESH_SECRET` e `JWT_REFRESH_EXPIRE_DAYS`. O serviço exige secrets seguros e distintos em qualquer ambiente antes de operar. Isso não altera a possibilidade de iniciar health/cadastro em development sem secrets, mas os valores fictícios de `.env.example` não permitem operações JWT.

A assinatura e a verificação usam PyJWT com algoritmo fixo `HS256`; o header do token não pode escolher outro algoritmo. Cada emissão gera um `jti` UUID novo. O payload contém exclusivamente `sub` (UUID do usuário), `type` (`access` ou `refresh`), `jti`, `iat` e `exp` (timestamps UTC em segundos). Não recebe CPF, senha, hashes, plano ou estado de licença.

Os decoders exigem todos os campos, verificam assinatura, expiração e data de emissão com PyJWT, validam UUIDs/timestamps e recusam tipo incorreto ou campos adicionais. Retornam um `TokenPayload` tipado e propagam erros PyJWT, como `ExpiredSignatureError`, `InvalidSignatureError` e `InvalidTokenError`. Configuração de secrets inválida gera `ValueError`. O serviço não registra tokens, payloads ou secrets.

As funções JWT isoladas não acessam PostgreSQL nem verificam revogação ou licença. Persistência e revogação são tratadas pelos serviços de login e refresh. JWT válido continua significando somente autenticação. O cadastro continua retornando usuário e assinatura PENDING sem emitir JWT.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_jwt.py -q
```

## Login HTTP — etapa 11

`POST /auth/login` recebe `email` e `password`. Requer o schema migrado, PostgreSQL acessível e dois secrets JWT seguros e distintos em Settings.

O email é validado com `EmailStr` e normalizado para lowercase. O serviço `app/modules/auth/login.py` busca o usuário sem diferenciar maiúsculas/minúsculas, exige `is_active = true` e verifica a senha com Argon2. Não reaplica a política de força de novas senhas. Conta ausente, inativa, senha incorreta ou hash inválido recebem a mesma resposta **401**:

```json
{"error": {"code": "INVALID_CREDENTIALS", "message": "Email ou senha inválidos."}}
```

Para contas ausentes/inativas, uma verificação Argon2 usa um hash fictício gerado por processo com `secrets.token_urlsafe`, reduzindo diferenças de trabalho entre tentativas. Não há logs de senha ou tokens. Dados de entrada inválidos retornam 422 sem ecoar o corpo recebido.

O sucesso retorna **200** com somente `access_token`, `refresh_token`, `token_type = bearer` e `expires_in` (duração do access em segundos). A resposta usa `Cache-Control: no-store` e `Pragma: no-cache`.

Antes de retornar os tokens, o serviço persiste `RefreshToken` com usuário, `expires_at` igual ao `exp` do JWT, `revoked_at = null` e somente o hash SHA-256 hexadecimal do refresh. O helper está em `app/shared/security/refresh_token.py`. SHA-256 é usado para esse token de alta entropia; senhas continuam usando Argon2. O refresh puro existe apenas durante a emissão e na resposta ao titular, nunca em coluna do banco. Cada login gera um novo token e registro.

A transação é confirmada antes da resposta. Falhas de flush/commit impedem a entrega dos tokens e retornam erro genérico (`AUTHENTICATION_FAILED`, HTTP 500; falhas operacionais ou configuração JWT inválida usam `AUTHENTICATION_UNAVAILABLE`, HTTP 503).

Login não significa licença ativa. O serviço não consulta nem modifica assinaturas: contas ativas podem autenticar mesmo sem assinatura ou com status `PENDING`, `EXPIRED`, `SUSPENDED` ou `CANCELLED`. Os JWTs não contêm plano ou status de licença. Funcionalidades protegidas deverão consultar a validade da licença pela API nas etapas futuras.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_login.py -q
```

Os testes cobrem credenciais, normalização, estados de assinatura, tokens válidos, digest persistido e falhas transacionais. O teste PostgreSQL faz rollback dos dados criados; sem `DATABASE_URL`, ele é ignorado.

## Rotação de refresh — etapa 12

`POST /auth/refresh` recebe `{"refresh_token": "<token recebido no login>"}` e retorna HTTP 200 com novos `access_token`, `refresh_token`, `token_type` e `expires_in`. A resposta usa `Cache-Control: no-store` e `Pragma: no-cache`.

O serviço `app/modules/auth/refresh.py` valida assinatura, expiração e tipo do JWT, verifica a conta ativa e busca o registro pelo hash SHA-256 e pelo usuário do token. O registro deve existir, estar dentro de `expires_at` e ter `revoked_at = null`.

A conta é lida com lock compartilhado; o registro do refresh recebe `SELECT ... FOR UPDATE` e é atualizado a partir do banco após obter o lock. Assim, solicitações concorrentes com o mesmo token observam sua revogação após a primeira rotação confirmada. Os prazos do JWT e do registro são reavaliados depois da espera pelo lock.

Em uma única transação, o token atual recebe `revoked_at`, um novo refresh é emitido e somente seu hash é inserido, e um novo access é gerado. O commit ocorre antes da entrega dos tokens. Qualquer falha reverte a revogação e a inserção, mantendo o token anterior utilizável quando a rotação não foi confirmada. Após sucesso, o cliente deve substituir seu refresh pelo novo; o anterior não pode ser usado novamente.

JWT expirado/adulterado, access token enviado no lugar do refresh, conta ausente/desativada, registro ausente/expirado/revogado ou reutilização retornam HTTP **401**, com `INVALID_REFRESH_TOKEN` e mensagem genérica. Entrada malformada recebe 422; falha operacional/configuração usa 503, e outras falhas de persistência usam 500. Nenhum erro expõe o token recebido ou detalhes do banco.

A rotação não altera assinaturas nem concede licença. O schema existente de `refresh_tokens` é suficiente; não há migration nova.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_refresh.py -q
```

Os testes isolados cobrem sucesso, expiração JWT/banco, revogação, reuso, usuário inativo, adulteração, tipo incorreto e falhas de flush/commit. Os testes PostgreSQL verificam rotação, rejeição de reutilização e rollback real; sem `DATABASE_URL`, são ignorados.

## Logout HTTP — etapa 13

`POST /auth/logout` recebe `{"refresh_token": "<refresh atual da sessão>"}`. Após o commit, retorna somente HTTP **200** com `{"status": "ok"}` e `Cache-Control: no-store`. Não retorna tokens, hashes ou dados da sessão.

O serviço `app/modules/auth/logout.py` calcula SHA-256 do token apresentado e executa um `UPDATE` atômico no registro correspondente, somente se `revoked_at IS NULL`, definindo a data atual em UTC. Não recupera nem persiste o token puro. Repetições preservam a primeira data de revogação; chamadas concorrentes são serializadas pelo update do PostgreSQL.

Tokens desconhecidos ou já revogados também recebem 200, sem revelar se havia registro. O logout usa a correspondência exata do hash, sem depender da validade temporal do JWT, permitindo revogar tokens expirados e sessões de contas inativas. Um token adulterado não corresponde ao hash armazenado e não altera nenhuma sessão. Corpo ausente/inválido recebe 422; falhas de banco recebem erro genérico 500/503, sem reportar sucesso antes do commit.

A operação revoga apenas o refresh fornecido, não outras sessões ou um refresh novo emitido por rotação. O cliente deve enviar seu refresh mais recente e remover os tokens locais ao sair. O refresh revogado passa a ser rejeitado por `/auth/refresh`; access tokens já emitidos mantêm sua expiração curta, pois esta etapa não adiciona uma lista de revogação de access tokens. Nenhuma assinatura é alterada.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_logout.py -q
```

Os testes isolados cobrem resposta sem tokens, filtro por hash, repetição, entrada inválida e falhas transacionais. O teste PostgreSQL verifica revoked_at, repetição sem alterar a data, bloqueio de refresh e preservação de outra sessão. Sem `DATABASE_URL`, esse teste é ignorado.

## Serviço de licença — etapa 14

`app/modules/subscriptions/service.py` fornece `SubscriptionService(session).get_license_status(user_id: UUID)`. Use uma sessão sem transação já iniciada: o serviço abre uma transação, consulta os dados atuais, confirma alterações de expiração e retorna `LicenseStatus`. O schema de saída está em `app/modules/subscriptions/schemas/license.py` e contém somente `active`, `status`, `plan` (code/name), `starts_at`, `expires_at` e `is_lifetime`.

Toda autorização futura de funcionalidades protegidas do DTF Manager deve consultar este serviço e exigir `active = true`. Não reutilizar claims JWT ou uma decisão antiga como prova de licença. O schema inicial `LicenseRead` permanece disponível, mas a decisão centralizada é retornada em `LicenseStatus`.

| Condição | Resultado |
| --- | --- |
| Usuário ausente ou sem assinatura | active false; status e plan nulos |
| Conta desativada | active false |
| PENDING, CANCELLED, SUSPENDED ou EXPIRED | active false, sem reativação automática |
| ACTIVE comum com expires_at <= hora da API | Persiste EXPIRED; active false |
| ACTIVE comum com início já atingido e vencimento futuro | active true para conta ativa |
| ACTIVE comum sem início ou sem vencimento | active false |
| ACTIVE LIFETIME com expires_at null | active true para conta ativa |
| LIFETIME com vencimento preenchido | active false; dados inconsistentes não concedem acesso |

O relógio é `datetime.now(UTC)` no servidor, lido após obter os locks; o método não recebe data do cliente. Datas com outros offsets são comparadas pelo mesmo instante. Na igualdade exata com o vencimento, a assinatura comum já está expirada. Consultas repetidas não reativam status EXPIRED. O serviço não usa `plan.is_active` para cancelar licenças adquiridas: esse campo controla disponibilidade do catálogo.

Como o schema permite várias assinaturas por usuário, o serviço avalia todas e retorna a válida mais recente (created_at e UUID como desempate). Uma nova assinatura PENDING não cancela uma licença paga vigente. Se nenhuma concede acesso, retorna a mais recente com active false. Assinaturas comuns ACTIVE vencidas são atualizadas mesmo quando outra assinatura concede acesso.

A conta recebe lock compartilhado e as assinaturas recebem `FOR UPDATE`, com atualização dos objetos a partir do banco. As expirações são persistidas em uma única transação, antes da resposta. Falhas de banco/commit geram `LicenseStatusUnavailable` e nunca retornam autorização. Não há cache, novo endpoint, ativação após pagamento ou alteração no aplicativo nesta etapa.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_subscription_service.py -q
```

Os testes abrangem todos os estados, conta desativada, vitalício, datas ausentes, limites de início/vencimento, timezone, múltiplas assinaturas e falhas de persistência. O teste PostgreSQL confirma a atualização real para EXPIRED e reverte os dados de teste; sem `DATABASE_URL`, é ignorado.

## Ativação interna — etapa 15

`SubscriptionService(session).activate_subscription(subscription_id: UUID)` executa a ativação inicial e retorna `SubscriptionRead` após confirmar a transação. O método é interno: seu chamador deve ter confirmado o pagamento. Não há endpoint, webhook ou integração de pagamento nesta etapa, e cadastro/login não chamam a ativação.

Para uma assinatura PENDING nunca ativada, `starts_at` e `activated_at` recebem a mesma hora atual da API em UTC, e `status` passa a ACTIVE:

| Plano | expires_at |
| --- | --- |
| MONTHLY | Hora inicial + 1 mês de calendário |
| SEMIANNUAL | Hora inicial + 6 meses de calendário |
| ANNUAL | Hora inicial + 12 meses de calendário |
| LIFETIME | null |

`app/shared/utils/calendar.py` usa `calendar.monthrange` e `datetime.replace` para adicionar meses e limitar o dia ao último dia válido do mês de destino, preservando horário e timezone. Por exemplo, 31/01/2024 + 1 mês resulta em 29/02/2024; 29/02/2024 + 12 meses resulta em 28/02/2025. Não há aproximação por quantidade fixa de dias.

A assinatura recebe `FOR UPDATE` e é relida após o lock. Repetições preservam integralmente as datas e o estado se `activated_at` já estiver preenchido ou se o status já for ACTIVE. Isso também protege registros legados ACTIVE sem activated_at e impede reativação indevida após cancelamento, suspensão ou expiração. Estados não PENDING sem ativação prévia são rejeitados. Este método não renova assinaturas.

A consulta, atualização e flush ocorrem em uma transação. Falhas de banco geram `SubscriptionActivationError` e rollback. Quando chamada isoladamente, a ativação confirma sua própria transação. Dentro de uma transação existente, usa savepoint e deixa o commit final para o chamador: PaymentService aprova pagamento e ativa assinatura atomicamente. Não há migration nova.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_activation.py -q
```

Os testes cobrem os quatro planos, limites de calendário, anos bissextos, repetição com relógio avançado, estados posteriores e falhas de persistência. O teste PostgreSQL verifica a ativação persistida e sua idempotência; sem `DATABASE_URL`, é ignorado.

## Endpoints Bearer — etapa 16

`app/modules/auth/dependencies.py` fornece `get_access_subject`, `get_current_user` e `get_current_license`. O FastAPI usa `HTTPBearer` documentado no OpenAPI como `AccessToken`. Envie exclusivamente o access token no header:

```http
Authorization: Bearer <access_token>
```

Assinatura, expiração, tipo access e UUID são verificados antes de consultar o banco. O `sub` validado determina a conta, que deve existir e estar ativa. Ausência de Bearer, token inválido/expirado, refresh token no lugar de access ou conta ausente/desativada retornam HTTP 401 com `INVALID_ACCESS_TOKEN` e `WWW-Authenticate: Bearer`. Erros não ecoam o token. Não há autenticação por cookie ou query.

Os dois endpoints não declaram body nem parâmetros `user_id`. Valores extras enviados no body/query não participam da decisão: a conta e sua licença são sempre consultadas pelo UUID autenticado. O DTO de identidade contém somente id, name e email; nenhum model completo é retornado.

**GET /license/status** chama `SubscriptionService.get_license_status` e retorna seu `LicenseStatus`, com `active`, `status`, `plan` (code/name), `starts_at`, `expires_at` e `is_lifetime`. A expiração é consultada e persistida pelo serviço. Contas autenticadas com assinatura PENDING, EXPIRED, SUSPENDED ou CANCELLED podem consultar o endpoint e recebem HTTP 200 com `active=false`; essa consulta não concede autorização de uso do DTF Manager.

**GET /me** retorna somente `id`, `name`, `email` e `subscription`, contendo `status`, `plan` (code/name), `starts_at` e `expires_at`. O resumo usa a mesma decisão do SubscriptionService; sem assinatura, os quatro campos ficam nulos. CPF, hashes, secrets, tokens e dados de pagamento não fazem parte da resposta.

As duas respostas usam `Cache-Control: no-store`. A leitura de identidade encerra sua transação antes da transação do serviço de licença, evitando conflitos de `Session.begin()`. Falhas na consulta da licença retornam HTTP 503 com `LICENSE_STATUS_UNAVAILABLE`, sem conceder acesso. Outras funcionalidades protegidas deverão exigir a decisão `active=true` desse mesmo serviço.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_account_endpoints.py -q
```

Os testes verificam autenticação, rejeição de refresh/assinatura inválida/token expirado, conta desativada, isolamento contra outro user_id, ausência de campos sensíveis, estados de licença e documentação Bearer. O teste PostgreSQL exercita as duas leituras e a expiração persistida; sem `DATABASE_URL`, ele é ignorado. A ativação continua sem endpoint público.

## Arquitetura de pagamentos — etapa 17

O módulo `app/modules/payments` contém `PaymentProvider` abstrato (`provider.py`), contratos internos (`contracts.py`) e `PaymentService` (`service.py`). A etapa 18 adiciona provider Mercado Pago e webhook, conforme documentação acima.

`PaymentProvider` define `name`, `create_payment(..., idempotency_key=...)` e `fetch_payment(provider_payment_id)`. Adaptadores futuros deverão validar confirmações junto ao provedor. O PaymentService não chama essas operações nesta etapa e não aceita uma declaração do aplicativo como confirmação de pagamento.

Métodos internos do serviço:

- `create_payment(PaymentCreate)`: verifica o vínculo usuário/assinatura e cria registro PENDING, sem ativar licença.
- `update_payment(payment_id, PaymentUpdate)`: atualiza status e permite vincular um ID externo ainda ausente. Aprovação passa obrigatoriamente pela ativação.
- `process_approval(payment_id)`: aplica uma aprovação definitiva já confirmada por um chamador confiável da API.
- `apply_provider_payment(payment_id, provider, ProviderPayment)`: confere provedor, valor e moeda de um resultado confiável antes de aplicar o status.

Valores monetários exigem `Decimal`, não aceitam float/string, NaN, infinito, negativos ou mais de duas casas decimais e respeitam `Numeric(18,2)`. Provider é normalizado para lowercase; currency, para três letras maiúsculas. Valor, moeda, usuário e assinatura não podem ser alterados reutilizando um identificador de pagamento.

A criação usa `INSERT ... ON CONFLICT DO NOTHING` com as constraints existentes. `(provider, provider_payment_id)` identifica um pagamento externo; o mesmo ID em provedores distintos não é duplicidade. Sem ID externo, forneça o mesmo `payment_id` UUID nas tentativas repetidas. Sem nenhuma dessas identidades estáveis, cada chamada representa um novo pagamento. Um ID externo pode ser associado depois, mas não substituído; colisões geram `PaymentConflict`.

Atualizações bloqueiam o pagamento com `FOR UPDATE`. A primeira transição para APPROVED chama `SubscriptionService.activate_subscription()` na mesma transação, usando savepoint interno. Qualquer falha reverte pagamento e assinatura. Aprovação repetida é um no-op para ativação e não estende o período. A ativação também é idempotente caso mais de um pagamento legítimo esteja relacionado à mesma assinatura.

Transições iniciais: PENDING → APPROVED/REJECTED/CANCELLED; APPROVED → REFUNDED. Repetir o mesmo status é permitido. Estados terminais não voltam para PENDING/APPROVED; notificações incompatíveis geram `PaymentConflict`. O registro REFUNDED não cancela automaticamente a licença nesta etapa: políticas de estorno/revogação ainda precisam ser definidas. Apenas aprovar aciona a ativação.

Os métodos públicos de PaymentService recebem uma sessão sem transação já iniciada e retornam `PaymentRecord` após commit, sem expor metadados internos. São comandos internos, sem autorização para chamada direta pelo aplicativo. Falhas geram `PaymentError`/`PaymentConflict` com mensagens seguras.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_payment_service.py -q
```

Os testes cobrem duplicidades, dinheiro, vínculo com a assinatura, atualizações, aprovação repetida e falha na ativação. Os testes PostgreSQL verificam aprovação/ativação persistidas e rollback conjunto; sem `DATABASE_URL`, são ignorados. Nenhum schema de banco ou migration foi alterado.

## Cadastro e planos

O usuário cria uma conta informando nome, CPF, email, senha e plano escolhido.

| Plano | Duração |
| --- | --- |
| `MONTHLY` | 1 mês |
| `SEMIANNUAL` | 6 meses |
| `ANNUAL` | 1 ano |
| `LIFETIME` | Vitalício |

Escolher um plano NÃO significa possuir licença ativa. No cadastro, a assinatura (`Subscription`) deverá ser criada com status `PENDING`. Somente após pagamento confirmado seu status deverá passar para `ACTIVE`.

## Assinatura e licença

Cada assinatura deverá possuir:

- Plano.
- Status.
- Data de início (`starts_at`).
- Data de vencimento (`expires_at`).
- Usuário associado.

A assinatura vitalícia possui `expires_at = null`. Isso não dispensa o pagamento confirmado nem a verificação do status da assinatura.

A validade da licença será sempre consultada no banco através da API, considerando o status e a vigência da assinatura. Uma assinatura `PENDING` não concede acesso licenciado; uma assinatura com prazo vencido também não concede acesso, mesmo que seu status ainda esteja registrado como `ACTIVE`.

Nunca gerar uma licença a partir de CPF, email ou nome.

## Autenticação e segurança

- Nunca armazenar senhas em texto puro. Utilizar hash Argon2.
- Nunca colocar secrets no código ou versioná-los. Configurações sensíveis deverão ser fornecidas externamente e carregadas com pydantic-settings.
- JWT de acesso tem duração configurável de 15 minutos por padrão. O prazo configurável de refresh é de 30 dias; login e refresh emitem tokens, com rotação de uso único no refresh.
- JWT representa autenticação e NÃO a duração da licença.
- Um JWT válido não comprova licença ativa e não substitui a consulta ao banco pela API.
- Toda comunicação do aplicativo com a API deverá ocorrer por HTTPS.

## Integrações e uso futuro

A integração inicial Mercado Pago valida pagamentos pela API oficial; a escolha de plano ou uma declaração do aplicativo não confirma pagamento.

O aplicativo DTF Manager posteriormente utilizará a API para:

- Login.
- Refresh de sessão.
- Logout.
- Verificação da licença.
- Consulta dos dados da conta.

A lógica central de licença está implementada em SubscriptionService e exposta por /license/status e pelo resumo de /me. Integração com funcionalidades do aplicativo fica para etapas posteriores. Cadastro, login, rotação de refresh, logout e webhook possuem endpoints.

## Instruções para desenvolvimento

As regras permanentes estão em [AGENTS.md](AGENTS.md). A etapa 18 autoriza integração inicial Mercado Pago e testes, preservando autenticação e licença, sem alterações no aplicativo DTF Manager.
