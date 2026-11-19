# API de Licenças — contrato para o DTF Manager

Contrato HTTP usado pelo aplicativo desktop para cadastro, login, validação da
licença e ativação de chaves, e pelo administrador para gerenciar licenças.
Toda comunicação deve usar HTTPS. Datas são ISO 8601 em UTC
(ex.: `2026-11-26T14:03:11.512345Z`).

## Conceitos

- Cada conta tem **uma** licença. Ela fica presa a **uma máquina** (`id_maquina`)
  e ao **telefone** informado no cadastro.
- Planos (`plano`): `TESTE` (7 dias), `MENSAL` (1 mês), `SEMESTRAL` (6 meses),
  `ANUAL` (12 meses), `VITALICIO` (sem expiração). Meses são de calendário
  (31/jan + 1 mês = 28/fev).
- Status da licença (`status`): `ATIVA`, `EXPIRADA`, `BLOQUEADA`.
- A validade conta a partir da **ativação**, nunca da geração da chave.
- Toda decisão usa o relógio do **servidor**. Use `servidor_agora` das respostas;
  não confie no relógio do PC.
- O JWT (`access_token`) só autentica a conta. A licença é sempre validada pela
  API em `/auth/login` e `/licencas/status`.

### `id_maquina`

Identificador estável do PC, gerado pelo aplicativo (por exemplo, um hash do
hardware ou um UUID gravado na instalação). Regras: de 8 a 128 caracteres entre
`a-z 0-9 . _ : { } -`. A API remove espaços externos e converte para minúsculas.
O mesmo PC precisa enviar sempre o mesmo valor; se o valor mudar, o PC é tratado
como outra máquina e a licença recusa com `MAQUINA_DIFERENTE`.

### Telefone

Somente telefones brasileiros com DDD. Aceita formatação e o prefixo `+55`:
`(11) 91234-5678`, `11912345678` e `+55 11 91234-5678` são equivalentes. É
gravado só com dígitos (`11912345678`). Celular: DDD + 9 + 8 dígitos. Fixo:
DDD + 8 dígitos começando em 2 a 5.

## Objeto de licença

```json
{
  "plano": "MENSAL",
  "status": "ATIVA",
  "data_ativacao": "2026-11-19T18:00:00Z",
  "data_expiracao": "2026-12-26T18:00:00Z",
  "dias_restantes": 37,
  "vitalicia": false
}
```

| Campo | Descrição |
| --- | --- |
| `plano` | `TESTE`, `MENSAL`, `SEMESTRAL`, `ANUAL` ou `VITALICIO` |
| `status` | `ATIVA`, `EXPIRADA` ou `BLOQUEADA` |
| `data_ativacao` | Última ativação (cadastro do teste ou última chave aplicada) |
| `data_expiracao` | `null` no vitalício |
| `dias_restantes` | Dias inteiros, arredondados para cima; `0` se vencida; `null` no vitalício |
| `vitalicia` | `true` quando o plano é `VITALICIO` |

## Formato de erro

Todos os erros têm `error.code` (estável, para o app tratar) e `error.message`
(texto em português, pronto para exibir). Erros de licença ligados a uma licença
existente também trazem `licenca` e `servidor_agora`:

```json
{
  "error": { "code": "LICENCA_EXPIRADA", "message": "Sua licença expirou. Ative uma nova chave para continuar." },
  "licenca": { "plano": "TESTE", "status": "EXPIRADA", "data_ativacao": "...", "data_expiracao": "...", "dias_restantes": 0, "vitalicia": false },
  "servidor_agora": "2026-11-26T18:00:01Z"
}
```

## Fluxo recomendado no aplicativo

1. **Primeiro uso:** `POST /auth/register` cria a conta e o teste de 7 dias
   nesta máquina. Em seguida, `POST /auth/login`.
2. **Login:** `POST /auth/login` com `id_maquina`. Em caso de sucesso, guarde os
   tokens e a `licenca`.
3. **A cada abertura:** `GET /licencas/status` com o Bearer e o cabeçalho
   `X-Id-Maquina`. Libere o uso só com HTTP 200. Se o access expirou (401), use
   `POST /auth/refresh` e repita.
4. **Licença expirada ou ausente:** o login recusa (`LICENCA_EXPIRADA` /
   `LICENCA_NAO_ENCONTRADA`) e não emite tokens. Peça a chave e chame
   `POST /licencas/ativar-chave` com `email` e `senha` no corpo. Depois, faça
   login de novo.
5. **Renovação antecipada:** com sessão válida, `POST /licencas/ativar-chave`
   com Bearer.

## Rotas do aplicativo

### `POST /auth/register` (pública, com rate limit)

```json
{
  "nome": "Maria Silva",
  "telefone": "(11) 91234-5678",
  "email": "maria@example.com",
  "senha": "Exemplo123",
  "id_maquina": "c0ffee00-1234-4abc-9def-001122334455"
}
```

- `nome` (ou `name`) tem de 1 a 200 caracteres.
- `senha` (ou `password`) precisa ter no mínimo 8 caracteres, com ao menos uma letra e um número.
- `cpf` e `plan_code` são opcionais e só servem ao fluxo de pagamento
  Mercado Pago. Se `plan_code` for enviado, também é criada a assinatura
  `PENDING` desse fluxo.
- O cadastro cria a conta e a licença `TESTE` em uma única transação. Ele só
  acontece se **nem** a máquina **nem** o telefone já tiverem usado um teste,
  mesmo que a conta antiga tenha sido apagada.

**201 Created**

```json
{
  "user": { "id": "…", "name": "Maria Silva", "email": "maria@example.com", "is_active": true,
            "created_at": "…", "updated_at": "…" },
  "subscription": null,
  "licenca": { "plano": "TESTE", "status": "ATIVA", "data_ativacao": "…", "data_expiracao": "…",
               "dias_restantes": 7, "vitalicia": false }
}
```

O cadastro não devolve tokens; faça o login em seguida.

**Erros:**
- `TESTE_JA_UTILIZADO` (409)
- `MAQUINA_EM_USO` (409)
- `EMAIL_ALREADY_EXISTS` (409)
- `CPF_ALREADY_EXISTS` (409)
- `TELEFONE_INVALIDO` (422)
- `ID_MAQUINA_INVALIDO` (422)
- `WEAK_PASSWORD` (422)
- `INVALID_CPF` (422)
- `INVALID_PLAN` (422)
- `VALIDATION_ERROR` (422)
- `MUITAS_TENTATIVAS` (429)
- `REGISTRATION_UNAVAILABLE` (503)
- `REGISTRATION_FAILED` (500)

### `POST /auth/login` (pública, com rate limit)

```json
{ "email": "maria@example.com", "senha": "Exemplo123", "id_maquina": "c0ffee00-1234-4abc-9def-001122334455" }
```

Valida as credenciais, a licença, a expiração e a máquina. Na primeira
validação, ou logo após um reset, a licença é vinculada à máquina enviada.

**200 OK**

```json
{
  "access_token": "…",
  "refresh_token": "…",
  "token_type": "bearer",
  "expires_in": 900,
  "licenca": { "plano": "TESTE", "status": "ATIVA", "data_ativacao": "…", "data_expiracao": "…",
               "dias_restantes": 6, "vitalicia": false },
  "servidor_agora": "2026-11-20T12:00:00Z"
}
```

**Erros** (nenhum deles emite tokens):
- `INVALID_CREDENTIALS` (401): e-mail ou senha inválidos; a mensagem é genérica.
- `ID_MAQUINA_INVALIDO` (422): `id_maquina` ausente ou inválido.
- `MAQUINA_DIFERENTE` (403)
- `MAQUINA_EM_USO` (409)
- `LICENCA_EXPIRADA` (403): o status é gravado como `EXPIRADA`.
- `LICENCA_BLOQUEADA` (403)
- `LICENCA_NAO_ENCONTRADA` (404)
- `MUITAS_TENTATIVAS` (429)
- `AUTHENTICATION_UNAVAILABLE` (503)
- `AUTHENTICATION_FAILED` (500)

Contas de administrador não precisam de `id_maquina` e nunca são recusadas por
licença.

### `GET /licencas/status` (Bearer)

Cabeçalhos: `Authorization: Bearer <access_token>` e
`X-Id-Maquina: <id_maquina>`. Não tem corpo.

**200 OK**

```json
{ "licenca": { "plano": "ANUAL", "status": "ATIVA", "data_ativacao": "…", "data_expiracao": "…",
               "dias_restantes": 300, "vitalicia": false },
  "servidor_agora": "…" }
```

**Erros:**
- `INVALID_ACCESS_TOKEN` (401): use o refresh e tente de novo.
- `ID_MAQUINA_INVALIDO` (422)
- `MAQUINA_DIFERENTE` (403)
- `MAQUINA_EM_USO` (409)
- `LICENCA_EXPIRADA` (403)
- `LICENCA_BLOQUEADA` (403)
- `LICENCA_NAO_ENCONTRADA` (404)
- `LICENCA_INDISPONIVEL` (503)

O app **nunca** deve liberar o uso diante de erro ou falha de rede.

### `POST /licencas/ativar-chave` (Bearer, ou e-mail e senha; com rate limit)

Com sessão válida, envie o Bearer:

```json
{ "chave": "K7QM-2XTA-9PHD-WR4E", "id_maquina": "c0ffee00-1234-4abc-9def-001122334455" }
```

Sem sessão (o login foi recusado por licença expirada ou ausente), envie sem
Bearer:

```json
{ "chave": "K7QM-2XTA-9PHD-WR4E", "id_maquina": "c0ffee00-…", "email": "maria@example.com", "senha": "Exemplo123" }
```

A chave aceita minúsculas, espaços e hífens. Ela é de uso único: depois de
ativada, nunca mais vale.

Como o plano é aplicado:
- **Licença ainda válida:** a duração do plano é somada à `data_expiracao` atual.
- **Licença vencida:** a duração conta a partir de agora.
- **Chave `VITALICIO`:** remove a expiração.
- **Licença que já é vitalícia:** recusa chaves de outros planos
  (`LICENCA_JA_VITALICIA`) e mantém a chave disponível.
- **Conta antiga sem licença:** a chave cria a licença vinculada a esta máquina.

**200 OK**

```json
{ "licenca": { "plano": "MENSAL", "status": "ATIVA", "data_ativacao": "…", "data_expiracao": "…",
               "dias_restantes": 37, "vitalicia": false },
  "servidor_agora": "…", "plano_ativado": "MENSAL" }
```

**Erros:**
- `CHAVE_INVALIDA` (422): chave inexistente, já usada, cancelada ou mal formatada.
- `MAQUINA_DIFERENTE` (403)
- `MAQUINA_EM_USO` (409)
- `LICENCA_BLOQUEADA` (403)
- `LICENCA_JA_VITALICIA` (409)
- `ID_MAQUINA_INVALIDO` (422)
- `INVALID_CREDENTIALS` / `INVALID_ACCESS_TOKEN` (401)
- `MUITAS_TENTATIVAS` (429)
- `LICENCA_INDISPONIVEL` (503)

### Rotas existentes que continuam iguais

`POST /auth/refresh`, `POST /auth/logout`, `GET /me`, `GET /plans`,
`POST /payments/checkout` e `GET /payments/{id}` não mudaram. O refresh **não**
valida a licença; por isso `GET /licencas/status` deve ser chamado a cada
abertura. `GET /license/status` e `GET /me` continuam refletindo a
**assinatura de pagamento** (Mercado Pago), não a licença do aplicativo.

## Rotas do administrador

Todas exigem `Authorization: Bearer <access_token>` de uma conta com perfil
`ADMIN`. Uma conta comum recebe `ACESSO_NEGADO` (403). O administrador faz login
por `POST /auth/login` sem `id_maquina`.

| Método e caminho | Corpo / query | Resposta |
| --- | --- | --- |
| `POST /admin/chaves` | `{"plano", "nome_cliente", "contato_cliente"}` | 201 `ChaveGerada` (inclui `chave` completa, **só nesta resposta**) |
| `GET /admin/chaves` | `status`, `plano`, `busca` (nome/contato), `limite` (1–200, padrão 50), `deslocamento` | `{"total", "itens": [ChaveAdmin]}` |
| `POST /admin/chaves/{id}/cancelar` | — | `ChaveAdmin`; só chaves `DISPONIVEL` (senão `CHAVE_INVALIDA` 409) |
| `GET /admin/licencas` | `status`, `plano`, `busca` (nome, e-mail ou telefone), `limite`, `deslocamento` | `{"total", "itens": [LicencaAdmin]}` |
| `POST /admin/licencas/{id}/bloquear` | — | `LicencaAdmin` |
| `POST /admin/licencas/{id}/desbloquear` | — | `LicencaAdmin` (volta para `ATIVA` ou `EXPIRADA`, conforme a data) |
| `POST /admin/licencas/{id}/resetar-maquina` | `{"forcar": false}` (opcional) | `LicencaAdmin`; acima de 3 resets em 12 meses: `LIMITE_RESETS` (409), a menos que `forcar: true` |
| `PATCH /admin/licencas/{id}` | `{"plano"?, "data_expiracao"?}` | `LicencaAdmin`; `VITALICIO` remove a expiração; incompatível: `ALTERACAO_INVALIDA` (422) |
| `GET /admin` | — | Página HTML do painel (listar licenças e chaves, gerar, bloquear, desbloquear, resetar, cancelar) |

`ChaveAdmin`:

```json
{ "id": "…", "chave_mascarada": "****-****-****-WR4E", "plano": "ANUAL", "status": "DISPONIVEL",
  "nome_cliente": "Maria Silva", "contato_cliente": "(11) 91234-5678",
  "licenca_id": null, "criada_em": "…", "usada_em": null, "cancelada_em": null }
```

`LicencaAdmin`:

```json
{ "id": "…", "usuario": { "id": "…", "nome": "Maria Silva", "email": "maria@example.com" },
  "plano": "TESTE", "status": "ATIVA", "data_ativacao": "…", "data_expiracao": "…", "dias_restantes": 7,
  "id_maquina": "c0ffee00-…", "telefone": "11912345678", "contador_resets": 0,
  "data_ultimo_reset": null, "criado_em": "…", "atualizado_em": "…" }
```

O reset limpa `id_maquina`, soma 1 em `contador_resets`, grava
`data_ultimo_reset` e registra o histórico (máquina anterior, quem fez e se foi
forçado). A próxima máquina que fizer login ou validar fica vinculada.

Outros erros das rotas admin: `INVALID_ACCESS_TOKEN` (401),
`LICENCA_NAO_ENCONTRADA` (404), `CHAVE_NAO_ENCONTRADA` (404),
`MAQUINA_EM_USO` (409, ao desbloquear ou ajustar uma licença cuja máquina já tem
outra licença ativa), `VALIDATION_ERROR` (422) e `LICENCA_INDISPONIVEL` (503).

## Tabela de códigos de erro

| Código | HTTP | Significado |
| --- | --- | --- |
| `TESTE_JA_UTILIZADO` | 409 | Máquina ou telefone já usou o período de teste |
| `MAQUINA_DIFERENTE` | 403 | Licença vinculada a outra máquina |
| `MAQUINA_EM_USO` | 409 | Máquina já tem licença ativa de outra conta |
| `ID_MAQUINA_INVALIDO` | 422 | `id_maquina` / `X-Id-Maquina` ausente ou inválido |
| `LICENCA_EXPIRADA` | 403 | Licença vencida (status gravado como `EXPIRADA`) |
| `LICENCA_BLOQUEADA` | 403 | Bloqueada pelo administrador |
| `LICENCA_NAO_ENCONTRADA` | 404 | Conta sem licença (ou licença inexistente, no admin) |
| `LICENCA_JA_VITALICIA` | 409 | Chave não vitalícia em licença vitalícia; chave preservada |
| `CHAVE_INVALIDA` | 422 / 409 | Chave inexistente, usada ou cancelada (409 ao cancelar chave não disponível) |
| `CHAVE_NAO_ENCONTRADA` | 404 | ID de chave inexistente (admin) |
| `LIMITE_RESETS` | 409 | 3 resets nos últimos 12 meses; inclui `resets_ultimos_12_meses` e `limite` |
| `TELEFONE_INVALIDO` | 422 | Telefone fora do formato brasileiro com DDD |
| `ALTERACAO_INVALIDA` | 422 | Plano e expiração incompatíveis no ajuste manual |
| `ACESSO_NEGADO` | 403 | Rota exclusiva de administrador |
| `MUITAS_TENTATIVAS` | 429 | Rate limit; respeite o cabeçalho `Retry-After` (segundos) |
| `LICENCA_INDISPONIVEL` | 503 | Falha temporária do banco; tente novamente e não libere o uso |
| `INVALID_CREDENTIALS` | 401 | E-mail ou senha inválidos |
| `INVALID_ACCESS_TOKEN` | 401 | Bearer ausente, inválido ou expirado |

## Rate limit

Cadastro, login e ativação de chave aceitam `RATE_LIMIT_REQUESTS` tentativas
(padrão 10) por IP a cada `RATE_LIMIT_WINDOW_SECONDS` (padrão 60), contadas
separadamente por rota. O limite vale também para requisições inválidas. O IP
vem do último salto de `X-Forwarded-For`, que é adicionado pelo proxy do
Railway. A contagem fica na memória do processo e zera ao reiniciar a API.

## Variáveis de ambiente novas

| Variável | Padrão | Uso |
| --- | --- | --- |
| `RATE_LIMIT_ENABLED` | `true` | Liga/desliga o rate limit |
| `RATE_LIMIT_REQUESTS` | `10` | Tentativas por janela, por IP e por rota |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | Tamanho da janela, em segundos |
| `RATE_LIMIT_TRUST_PROXY` | `true` | Usa o último salto de `X-Forwarded-For` como IP (Railway). Use `false` sem proxy. |
| `ADMIN_EMAIL` | — | Somente para `scripts/seed_admin.py` |
| `ADMIN_SENHA` | — | Somente para `scripts/seed_admin.py`; não é lida pelas rotas |
| `ADMIN_NOME` | `Administrador` | Nome do administrador ao criar a conta |
