"""Página HTML simples do administrador, servida pela API e usando as rotas /admin."""

import secrets

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router: APIRouter = APIRouter(tags=["admin"])

_PAGE: str = r"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Layera · Painel de licenças</title>
<style nonce="__NONCE__">
:root { --bg:#f6f7f9; --panel:#fff; --text:#1d232b; --muted:#5d6875; --line:#d9dee4; --accent:#1f5fbf;
        --danger:#b3261e; --ok:#1e7d43; --warn:#9a6700; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#14171c; --panel:#1c2027; --text:#e6e9ed; --muted:#9aa4b0; --line:#323944; --accent:#6ea2ff;
          --danger:#ff8a80; --ok:#6fd39b; --warn:#e8c06a; }
}
* { box-sizing: border-box; }
body { margin:0; font:14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; background:var(--bg); color:var(--text); }
header { display:flex; justify-content:space-between; align-items:center; padding:12px 20px; border-bottom:1px solid var(--line); background:var(--panel); }
h1 { font-size:16px; margin:0; } h2 { font-size:15px; margin:0 0 12px; }
main { max-width:1200px; margin:0 auto; padding:20px 16px; display:grid; gap:16px; }
section { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:16px; }
form.row, .row { display:flex; flex-wrap:wrap; gap:8px; align-items:end; }
label { display:grid; gap:4px; font-size:12px; color:var(--muted); }
input, select, button { font:inherit; padding:7px 10px; border:1px solid var(--line); border-radius:6px; background:var(--panel); color:var(--text); }
button { cursor:pointer; background:var(--accent); border-color:var(--accent); color:#fff; }
button.secondary { background:transparent; color:var(--text); border-color:var(--line); }
button.danger { background:transparent; color:var(--danger); border-color:var(--danger); }
button:disabled { opacity:.5; cursor:default; }
.table-wrap { overflow-x:auto; margin-top:12px; }
table { width:100%; border-collapse:collapse; font-size:13px; }
th, td { text-align:left; padding:7px 8px; border-bottom:1px solid var(--line); vertical-align:top; white-space:nowrap; }
th { color:var(--muted); font-weight:600; }
td.actions { display:flex; gap:6px; }
.muted { color:var(--muted); font-size:12px; }
.badge { font-size:12px; font-weight:600; }
.ATIVA, .DISPONIVEL { color:var(--ok); } .EXPIRADA, .USADA { color:var(--warn); } .BLOQUEADA, .CANCELADA { color:var(--danger); }
#message { min-height:20px; } #message.error { color:var(--danger); } #message.ok { color:var(--ok); }
.key { font:600 18px ui-monospace, Consolas, monospace; letter-spacing:1px; }
[hidden] { display:none !important; }
</style>
</head>
<body>
<header><h1>Layera · Licenças</h1><button id="logout" class="secondary" hidden>Sair</button></header>
<main>
  <div id="message" role="status"></div>

  <section id="login-section">
    <h2>Entrar como administrador</h2>
    <form id="login-form" class="row">
      <label>E-mail <input name="email" type="email" required autocomplete="username"></label>
      <label>Senha <input name="password" type="password" required autocomplete="current-password"></label>
      <button type="submit">Entrar</button>
    </form>
  </section>

  <div id="app" hidden>
    <section>
      <h2>Gerar chave de ativação</h2>
      <form id="key-form" class="row">
        <label>Plano <select name="plano" required>
          <option>MENSAL</option><option>SEMESTRAL</option><option>ANUAL</option><option>VITALICIO</option><option>TESTE</option>
        </select></label>
        <label>Nome do cliente <input name="nome_cliente" required maxlength="200"></label>
        <label>Contato do cliente <input name="contato_cliente" required maxlength="200"></label>
        <button type="submit">Gerar</button>
      </form>
      <div id="new-key" hidden>
        <p class="muted">Copie agora: a chave completa não pode ser consultada depois.</p>
        <div class="row"><span id="new-key-value" class="key"></span><button id="copy-key" class="secondary" type="button">Copiar</button></div>
      </div>
    </section>

    <section>
      <h2>Licenças</h2>
      <form id="license-filter" class="row">
        <label>Status <select name="status"><option value="">Todos</option><option>ATIVA</option><option>EXPIRADA</option><option>BLOQUEADA</option></select></label>
        <label>Plano <select name="plano"><option value="">Todos</option><option>TESTE</option><option>MENSAL</option><option>SEMESTRAL</option><option>ANUAL</option><option>VITALICIO</option></select></label>
        <label>Nome, e-mail, telefone ou indicação <input name="busca" maxlength="200"></label>
        <button type="submit">Filtrar</button>
      </form>
      <p id="license-total" class="muted"></p>
      <div class="table-wrap"><table>
        <thead><tr><th>Cliente</th><th>Indicação</th><th>Cadastro</th><th>Plano</th><th>Status</th><th>Expira</th><th>Dias</th><th>Máquina</th><th>Telefone</th><th>Resets</th><th>Ações</th></tr></thead>
        <tbody id="license-rows"></tbody>
      </table></div>
    </section>

    <section>
      <h2>Chaves</h2>
      <form id="key-filter" class="row">
        <label>Status <select name="status"><option value="">Todos</option><option>DISPONIVEL</option><option>USADA</option><option>CANCELADA</option></select></label>
        <label>Plano <select name="plano"><option value="">Todos</option><option>TESTE</option><option>MENSAL</option><option>SEMESTRAL</option><option>ANUAL</option><option>VITALICIO</option></select></label>
        <label>Nome ou contato <input name="busca" maxlength="200"></label>
        <button type="submit">Filtrar</button>
      </form>
      <p id="key-total" class="muted"></p>
      <div class="table-wrap"><table>
        <thead><tr><th>Chave</th><th>Plano</th><th>Status</th><th>Cliente</th><th>Contato</th><th>Criada</th><th>Usada</th><th>Ações</th></tr></thead>
        <tbody id="key-rows"></tbody>
      </table></div>
    </section>
  </div>
</main>
<script nonce="__NONCE__">
"use strict";
const TOKEN_KEY = "dtf_admin_token";
let token = null;
try { token = sessionStorage.getItem(TOKEN_KEY); } catch (e) { token = null; }
const $ = (id) => document.getElementById(id);

function show(text, kind) { const box = $("message"); box.textContent = text || ""; box.className = kind || ""; }
function fmt(value) { return value ? new Date(value).toLocaleString("pt-BR") : "—"; }
function setToken(value) {
  token = value;
  try { value ? sessionStorage.setItem(TOKEN_KEY, value) : sessionStorage.removeItem(TOKEN_KEY); } catch (e) {}
  $("login-section").hidden = !!value; $("app").hidden = !value; $("logout").hidden = !value;
}

async function api(method, path, body) {
  const headers = { "Accept": "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (token) headers["Authorization"] = "Bearer " + token;
  const response = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  let data = null;
  try { data = await response.json(); } catch (e) { data = null; }
  if (!response.ok) {
    const error = new Error((data && data.error && data.error.message) || "Falha na operação.");
    error.code = data && data.error ? data.error.code : null;
    error.status = response.status;
    if (response.status === 401 && path !== "/auth/login") { setToken(null); error.message = "Sessão expirada. Entre novamente."; }
    throw error;
  }
  return data;
}

function cell(row, text, className) { const td = document.createElement("td"); td.textContent = text; if (className) td.className = className; row.appendChild(td); return td; }
function button(label, className, handler) { const b = document.createElement("button"); b.type = "button"; b.textContent = label; b.className = className; b.addEventListener("click", handler); return b; }
function query(form) { const params = new URLSearchParams(); for (const [k, v] of new FormData(form)) if (v) params.set(k, v); params.set("limite", "200"); return params.toString(); }

async function loadLicenses() {
  const data = await api("GET", "/admin/licencas?" + query($("license-filter")));
  $("license-total").textContent = data.total + " licença(s)" + (data.total > data.itens.length ? " — exibindo " + data.itens.length : "");
  const body = $("license-rows"); body.replaceChildren();
  for (const item of data.itens) {
    const row = document.createElement("tr");
    const who = cell(row, item.usuario.nome); const email = document.createElement("div"); email.className = "muted"; email.textContent = item.usuario.email; who.appendChild(email);
    cell(row, item.usuario.indicacao || "—"); cell(row, fmt(item.usuario.cadastrado_em));
    cell(row, item.plano); cell(row, item.status, "badge " + item.status);
    cell(row, item.data_expiracao ? fmt(item.data_expiracao) : "Vitalícia");
    cell(row, item.dias_restantes === null ? "—" : String(item.dias_restantes));
    cell(row, item.id_maquina || "livre"); cell(row, item.telefone || "—");
    cell(row, item.contador_resets + (item.data_ultimo_reset ? " (último " + fmt(item.data_ultimo_reset) + ")" : ""));
    const actions = cell(row, "", "actions");
    if (item.status === "BLOQUEADA") actions.appendChild(button("Desbloquear", "secondary", () => act("POST", "/admin/licencas/" + item.id + "/desbloquear", undefined, "Licença desbloqueada.")));
    else actions.appendChild(button("Bloquear", "danger", () => { if (confirm("Bloquear a licença de " + item.usuario.nome + "?")) act("POST", "/admin/licencas/" + item.id + "/bloquear", undefined, "Licença bloqueada."); }));
    actions.appendChild(button("Resetar máquina", "secondary", () => resetMachine(item)));
    body.appendChild(row);
  }
}

async function loadKeys() {
  const data = await api("GET", "/admin/chaves?" + query($("key-filter")));
  $("key-total").textContent = data.total + " chave(s)" + (data.total > data.itens.length ? " — exibindo " + data.itens.length : "");
  const body = $("key-rows"); body.replaceChildren();
  for (const item of data.itens) {
    const row = document.createElement("tr");
    cell(row, item.chave_mascarada); cell(row, item.plano); cell(row, item.status, "badge " + item.status);
    cell(row, item.nome_cliente); cell(row, item.contato_cliente); cell(row, fmt(item.criada_em)); cell(row, fmt(item.usada_em));
    const actions = cell(row, "", "actions");
    if (item.status === "DISPONIVEL") actions.appendChild(button("Cancelar", "danger", () => { if (confirm("Cancelar esta chave?")) act("POST", "/admin/chaves/" + item.id + "/cancelar", undefined, "Chave cancelada."); }));
    body.appendChild(row);
  }
}

async function refresh() { await Promise.all([loadLicenses(), loadKeys()]); }

async function act(method, path, body, success) {
  try { await api(method, path, body); show(success, "ok"); await refresh(); }
  catch (error) { show(error.message, "error"); throw error; }
}

async function resetMachine(item) {
  if (!confirm("Liberar a máquina de " + item.usuario.nome + "? A próxima máquina que validar ficará vinculada.")) return;
  try { await act("POST", "/admin/licencas/" + item.id + "/resetar-maquina", { forcar: false }, "Máquina liberada."); }
  catch (error) {
    if (error.code === "LIMITE_RESETS" && confirm("Limite de 3 resets em 12 meses atingido. Forçar mesmo assim?")) {
      await act("POST", "/admin/licencas/" + item.id + "/resetar-maquina", { forcar: true }, "Máquina liberada (reset forçado).").catch(() => {});
    }
  }
}

$("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  try {
    const data = await api("POST", "/auth/login", { email: form.get("email"), password: form.get("password") });
    setToken(data.access_token);
    event.target.reset();
    await refresh();
    show("", "");
  } catch (error) {
    if (error.status === 403 && token) { setToken(null); show("Esta conta não tem perfil de administrador.", "error"); }
    else show(error.code === "ID_MAQUINA_INVALIDO" ? "Esta conta não tem perfil de administrador." : error.message, "error");
  }
});
$("key-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  try {
    const data = await api("POST", "/admin/chaves", { plano: form.get("plano"), nome_cliente: form.get("nome_cliente"), contato_cliente: form.get("contato_cliente") });
    $("new-key-value").textContent = data.chave; $("new-key").hidden = false;
    event.target.reset(); show("Chave gerada.", "ok"); await loadKeys();
  } catch (error) { show(error.message, "error"); }
});
$("copy-key").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText($("new-key-value").textContent); show("Chave copiada.", "ok"); }
  catch (e) { show("Não foi possível copiar; selecione a chave manualmente.", "error"); }
});
$("license-filter").addEventListener("submit", (event) => { event.preventDefault(); loadLicenses().catch((e) => show(e.message, "error")); });
$("key-filter").addEventListener("submit", (event) => { event.preventDefault(); loadKeys().catch((e) => show(e.message, "error")); });
$("logout").addEventListener("click", () => { setToken(null); $("new-key").hidden = true; show("", ""); });

setToken(token);
if (token) refresh().catch((e) => show(e.message, "error"));
</script>
</body>
</html>
"""


@router.get("/admin", response_class=HTMLResponse, include_in_schema=False)
def admin_panel() -> HTMLResponse:
    """A página não contém dados; tudo é carregado pelas rotas /admin com Bearer de ADMIN."""
    nonce: str = secrets.token_urlsafe(16)
    return HTMLResponse(_PAGE.replace("__NONCE__", nonce), headers={
        "Content-Security-Policy": (
            f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
            "connect-src 'self'; img-src 'self'; form-action 'none'; frame-ancestors 'none'; base-uri 'none'"
        ),
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    })
