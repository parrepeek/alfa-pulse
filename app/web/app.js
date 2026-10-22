// Альфа Пульс: интерфейс в стиле интернет-банка Альфа-Бизнес
const S = { user: null, me: null, group: null, role: null, feed: [], card: null };
const $ = (s) => document.querySelector(s);
const esc = (t) => String(t ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const api = async (path, opts) => {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
};
const q = (o) => new URLSearchParams(Object.fromEntries(Object.entries(o).filter(([, v]) => v != null))).toString();

async function start() {
  const users = await api("/api/users");
  $("#user-list").innerHTML = users.map((u) => `<button class="user-card" data-u="${u.user_id}">
      <b>${esc(u.role)}</b><span class="muted small">${esc(u.name)}</span><br><span class="small">${esc(u.group_label)}${u.groups > 1 ? ` · групп: ${u.groups}` : ""}</span></button>`).join("");
  document.querySelectorAll(".user-card").forEach((b) => (b.onclick = () => login(b.dataset.u)));
  const saved = localStorage.getItem("ap_user");
  if (saved && users.some((u) => u.user_id === saved)) login(saved);
}

async function login(uid) {
  S.user = uid;
  try { localStorage.setItem("ap_user", uid); } catch (e) {}
  S.me = await api("/api/me?" + q({ user: uid }));
  S.role = S.me.user.role;
  S.group = S.me.groups[0].group_id;
  $("#login").classList.add("hidden");
  $("#app").classList.remove("hidden");
  $("#user-badge").textContent = `${S.me.user.name} · ${S.role}`;
  $("#mode-badge").textContent = S.me.mode === "llm" ? "AI: разрешенные LLM" : "AI: режим без LLM-ключа";
  $("#group-select").innerHTML = S.me.groups.map((g) => `<option value="${g.group_id}">${esc(g.label)}</option>`).join("");
  $("#role-select").innerHTML = S.me.roles.map((r) => `<option ${r === S.role ? "selected" : ""}>${esc(r)}</option>`).join("");
  await loadFeed();
  show("home");
}

function show(view) {
  document.querySelectorAll(".view").forEach((v) => v.classList.add("hidden"));
  $("#view-" + view).classList.remove("hidden");
  document.querySelectorAll(".side a[data-view]").forEach((a) => a.classList.toggle("active", a.dataset.view === view));
  if (view === "profile") loadProfile();
  if (view === "quality") loadQuality();
}

async function loadFeed() {
  $("#feed").innerHTML = `<div class="spinner">Подбираем события для вашей группы…</div>`;
  const f = await api("/api/feed?" + q({ user: S.user, group: S.group, role: S.role }));
  S.feed = f.items;
  $("#nav-count").textContent = f.items.length || "";
  $("#w-count").textContent = f.items.length ? `${f.items.length} новых` : "";
  const top = f.items[0];
  $("#w-head").textContent = top ? top.title : "Значимых событий нет";
  $("#w-why").textContent = top ? "Почему вам: " + (top.why[0] || top.factor_title) : "";
  $("#feed-sub").textContent = `События, значимые для группы (${S.me.groups.find((g) => g.group_id === S.group).label}); роль: ${S.role}`;
  $("#feed").innerHTML = f.items.map((x, i) => `
    <div class="item" data-i="${i}">
      <div>
        <div class="meta"><span>${esc(x.date)}</span><span>${esc(x.source)}</span>
          ${x.kind.includes("Альфа") ? '<span class="chip kind-bank">Аналитика Альфа-Банка</span>' : ""}
          ${x.synthetic ? '<span class="chip kind-test">тест</span>' : ""}
          <span class="impact ${x.impact}">${x.impact === "нейтрально" ? "" : x.impact}</span></div>
        <h4>${esc(x.title)}</h4>
        <div><span class="chip">${esc(x.factor_title)}</span>${x.why.map((w) => `<span class="chip why">Почему вам: ${esc(w)}</span>`).join("")}</div>
      </div>
      <div class="rel"><span>значимость</span><b>${Math.round(Math.min(1, x.score) * 100)}</b><div class="bar"><i style="width:${Math.min(100, x.score * 100)}%"></i></div></div>
    </div>`).join("") || `<div class="block">Сейчас нет событий выше порога значимости: сервис не показывает ленту ради ленты.</div>`;
  document.querySelectorAll(".item").forEach((el) => (el.onclick = () => openCard(S.feed[+el.dataset.i].event_id)));
}

const lbl = (k) => ({ fact: '<span class="lbl fact">Факт</span>', bank: '<span class="lbl bank">Позиция Альфа-Банка</span>', ai: '<span class="lbl ai">Вывод AI</span>' }[k]);
const pageLink = (doc, page, title) => `<a onclick="openPage('${esc(doc)}', ${page})">${esc(title || doc)}, с. ${page}</a>`;

async function openCard(eventId, refresh = false) {
  $("#drawer").classList.remove("hidden");
  $("#card").innerHTML = `<div class="spinner">Агент анализирует событие: план → инструменты → черновик → самопроверка…</div>`;
  const c = await api("/api/insight?" + q({ user: S.user, group: S.group, event: eventId, role: S.role, refresh }));
  S.card = c;
  const wh = c.what_happened;
  $("#card").innerHTML = `
    <div class="meta">${esc(c.event.date)} · ${esc(c.event.source)} ${c.event.synthetic ? '<span class="chip kind-test">тест</span>' : ""}</div>
    <h3>${esc(c.event.title)}</h3>
    <div class="legend">${lbl("fact")} подтвержденная информация ${lbl("bank")} экспертиза банка ${lbl("ai")} сформировано моделью</div>

    <div class="block"><h4><span class="n">1</span> Что произошло</h4>
      ${wh.facts.map((f) => `<div class="stmt">${lbl("fact")}<div>${esc(f.text)}<div class="src">Источник: ${esc(f.source)}</div></div></div>`).join("")}
      <div class="src small muted">Статус: ${esc(wh.status)}${wh.also_reported_by.length ? "; также сообщили: " + esc(wh.also_reported_by.join(", ")) : ""}</div></div>

    <div class="block"><h4><span class="n">2</span> Почему это важно вашей группе</h4>
      ${c.why_it_matters.map((w) => `<div class="stmt">${lbl("ai")}<div>${esc(w.text)}<div class="src">Признаки клиента: ${esc((w.basis || []).join("; "))}</div></div></div>`).join("")}</div>

    <div class="block"><h4><span class="n">3</span> Какие последствия возможны</h4>
      ${c.impacts.map((i) => `<div class="stmt">${lbl("ai")}<div><span class="tag ${esc(i.type)}">${esc(i.type)}</span> ${esc(i.text)}
        <div class="src">${esc(i.mechanism || "")}${i.horizon ? " · горизонт: " + esc(i.horizon) : ""}</div></div></div>`).join("")}</div>

    <div class="block"><h4><span class="n">4</span> Как это оценивает Альфа-Банк</h4>
      ${c.bank_view.map((b) => `<div class="stmt">${lbl("bank")}<div>${esc(b.text)}<div class="src">${pageLink(b.doc_id, b.page, b.title)} · ${esc(b.date)} · «${esc(b.quote).slice(0, 220)}»</div></div></div>`).join("") || '<div class="muted">В материалах банка нет позиции по этой теме: выводы ниже сформированы моделью.</div>'}
      ${c.bank_notes.map((n) => `<div class="note">${esc(n)}</div>`).join("")}</div>

    <div class="block"><h4><span class="n">5</span> Что можно сделать</h4>
      ${c.actions.map((a) => `<div class="stmt">${lbl("ai")}<div>${esc(a.text)}${a.product ? `<span class="product">Предложение банка: ${esc(a.product)}</span>` : ""}</div></div>`).join("")}
      ${c.questions.length ? `<div class="note"><b>Что уточнить для точной оценки:</b><br>${c.questions.map(esc).join("<br>")}</div>` : ""}</div>

    <div class="block"><h4><span class="n">6</span> На чем основан вывод</h4>
      ${c.sources.map((s) => `<div class="stmt"><div><b>${esc(s.type)}:</b> ${esc(s.title)} <span class="src">(${esc(s.ref)}${s.date ? ", " + esc(s.date) : ""})</span></div></div>`).join("")}
      <div class="row gap" style="margin-top:8px"><button class="ghost" onclick="openTrace('${c.trace_id}')">Как получен вывод (трасса агента)</button>
      <span class="small muted">режим: ${esc(c.quality.mode)} · итераций: ${c.quality.iterations} · ${c.quality.ms} мс</span></div>
      <div class="checks" style="margin-top:8px">${c.quality.checks.map((k) => `<div class="${k.ok ? "ok" : "bad"}">${k.ok ? "✓" : "✗"} ${esc(k.name.replaceAll("_", " "))}</div>`).join("")}</div>
      <div id="trace"></div></div>

    <div class="block"><h4>Спросить про это событие</h4>
      <div class="suggest">${["Покажи по компаниям группы", "Какие контрагенты затронуты?", "А если ставка снизится?", "На чем основан вывод?"].map((s) => `<button onclick="ask('${s}')">${s}</button>`).join("")}</div>
      <div id="chat-log" class="chat-log"></div>
      <div class="row gap"><input id="chat-q" placeholder="Ваш вопрос"><button class="primary" onclick="ask($('#chat-q').value)">Спросить</button></div></div>

    <div class="block"><h4>Оценка карточки</h4><div class="fb">
      ${["полезно", "не про нас", "ошибка в факте"].map((k) => `<button class="ghost" onclick="feedback('${k}', this)">${k}</button>`).join("")}</div></div>`;
}

async function ask(text) {
  if (!text) return;
  const log = $("#chat-log");
  log.insertAdjacentHTML("beforeend", `<div class="msg q">${esc(text)}</div>`);
  $("#chat-q").value = "";
  const r = await api("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user: S.user, group: S.group, event: S.card.event.id, role: S.role, question: text }) });
  log.insertAdjacentHTML("beforeend", `<div class="msg a">${esc(r.answer)}<div class="src small muted">Основания: ${esc(r.refs.join("; "))}</div></div>`);
}

async function feedback(kind, btn) {
  await api("/api/feedback", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user: S.user, group: S.group, event: S.card.event.id, kind }) });
  btn.textContent = "✓ " + kind;
}

async function openTrace(id) {
  const t = await api(`/api/trace/${id}?` + q({ user: S.user }));
  $("#trace").innerHTML = t.steps.map((s) => `<div class="trace-step"><b>${esc(s.step)}</b>
    ${s.plan ? "<br>Факторы: " + esc(s.factors.join(", ")) + "<br>" + s.plan.map((p) => `• ${esc(p.tool)} — ${esc(p.why)}`).join("<br>") : ""}
    ${s.research ? "<br>Найдено в аналитике банка: " + esc(s.research.map((r) => r.join(" с.")).join("; ")) + "<br>Прогнозы: " + esc(s.forecasts.map((f) => f.join(" ")).join("; ")) : ""}
    ${s.reason ? "<br>Причина: " + esc(s.reason) + "; действие: " + esc(s.action) : ""}
    ${s.checks ? "<br>" + s.checks.map((c) => `${c.ok ? "✓" : "✗"} ${esc(c.name)} ${esc(c.detail)}`).join("<br>") : ""}
    ${s.critic && s.critic.length ? "<br>Критик: " + esc(s.critic.join("; ")) : ""}</div>`).join("") +
    `<div class="trace-step"><b>Вызовы инструментов (журнал)</b><br>${t.tool_calls.map((c) => `${c.ok ? "✓" : "✗"} ${esc(c.tool)} ${esc(JSON.stringify(c.args))} · ${c.ms} мс`).join("<br>")}</div>`;
}

function openPage(doc, page) { window.open(`/api/research/page/${encodeURIComponent(doc)}/${page}`, "_blank"); }

async function loadProfile() {
  const p = await api("/api/profile?" + q({ user: S.user, group: S.group }));
  const pct = (v) => Math.round(v * 100) + "%";
  $("#profile").innerHTML = `
    <div class="block"><div class="kv">
      <div><b>Основная отрасль:</b> ${esc(p.main_sector)}<br><span class="small muted">${esc(p.lead_okved)}</span></div>
      <div><b>Масштаб:</b> ${p.revenue_bn_rub} млрд руб., ${p.companies} компаний</div>
      <div><b>Отраслевая структура:</b> ${Object.entries(p.sector_mix).map(([k, v]) => `${esc(k)} ${pct(v)}`).join(", ")}</div>
      <div><b>Продукты банка (доля выручки):</b> ${Object.entries(p.products).filter(([, v]) => v > 0).map(([k, v]) => `${esc(k)} ${pct(v)}`).join(", ")}</div>
      <div><b>ВЭД по странам:</b> ${Object.entries(p.trade_countries_weight || {}).slice(0, 5).map(([k, v]) => `${esc(k.charAt(0) + k.slice(1).toLowerCase())} ${pct(v)}`).join(", ") || "нет данных"}</div>
      <div><b>Товары:</b> ${Object.entries(p.commodities).map(([k, v]) => `${esc(k)} ${pct(v)}`).join(", ") || "—"}</div>
      <div><b>Покупатели по отраслям:</b> ${Object.entries(p.customers_by_sector).map(([k, v]) => `${esc(k)} ${pct(v)}`).join(", ")}</div>
      <div><b>Качество данных:</b> контрагенты вне данных банка ${pct(p.data_quality.counterparties_outside_dataset || 0)}, дубли ID ${p.data_quality.dup_ids}</div>
    </div></div>
    <div class="block"><h4>Чувствительность к факторам</h4>
      ${Object.entries(p.exposure).sort((a, b) => b[1].score - a[1].score).map(([k, e]) => `<div class="expo"><span>${esc(p.factor_titles[k] || k)}</span>
        <div class="bar"><i style="width:${e.score * 100}%"></i></div><b>${e.score.toFixed(2)}</b><small>${esc(e.because.filter(Boolean).join("; "))}</small></div>`).join("")}</div>
    <div class="block"><h4>Уточнить профиль</h4>
      <p class="small muted">Например: «плавающая ставка» = нет, «хеджирование валюты» = есть. Карточки группы будут пересчитаны.</p>
      <div class="row gap"><input id="c-key" placeholder="признак"><input id="c-val" placeholder="значение"><button class="primary" onclick="saveCorr()">Сохранить</button></div>
      <div class="small">${Object.entries(p.client_corrections || {}).map(([k, v]) => `уточнено: ${esc(k)} = ${esc(v)}`).join("<br>")}</div></div>`;
}

async function saveCorr() {
  await api("/api/profile/correction", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user: S.user, group: S.group, key: $("#c-key").value, value: $("#c-val").value }) });
  await loadProfile(); await loadFeed();
}

async function loadQuality() {
  const m = await api("/api/quality-stats");
  $("#quality").innerHTML = `<div class="block"><div class="kv">
      <div><b>Карточек сформировано:</b> ${m.cards}</div><div><b>Средняя длительность:</b> ${m.avg_card_ms ?? "—"} мс</div>
      <div><b>Итераций самопроверки:</b> ${m.avg_iterations ?? "—"}</div><div><b>Токены за 24 ч:</b> ${m.tokens_24h} из ${m.token_limit}</div>
      <div><b>Новостей:</b> ${m.news}, дублей склеено: ${m.duplicates}</div><div><b>Обратная связь:</b> ${esc(JSON.stringify(m.feedback))}</div></div></div>
    <div class="block"><h4>Доля карточек, прошедших проверки</h4><table>${Object.entries(m.checks_pass_rate).map(([k, v]) => `<tr><td>${esc(k.replaceAll("_", " "))}</td><td>${Math.round(v * 100)}%</td></tr>`).join("")}</table></div>
    <div class="block"><h4>Инструменты и модели</h4><table><tr><th>инструмент</th><th>вызовов</th><th>мс</th><th>ошибок</th><th>токены</th></tr>
      ${m.tools.map((t) => `<tr><td>${esc(t.tool)}</td><td>${t.calls}</td><td>${t.avg_ms}</td><td>${t.errors}</td><td>${t.tokens}</td></tr>`).join("")}</table></div>
    <div class="block"><h4>Извлечение событий</h4>${esc(JSON.stringify(m.events_by_extractor))}</div>`;
}

$("#logout").onclick = () => { try { localStorage.removeItem("ap_user"); } catch (e) {} location.reload(); };
$("#drawer-close").onclick = () => $("#drawer").classList.add("hidden");
$("#drawer").onclick = (e) => { if (e.target.id === "drawer") $("#drawer").classList.add("hidden"); };
document.querySelectorAll(".side a[data-view]").forEach((a) => (a.onclick = () => show(a.dataset.view)));
document.querySelectorAll("[data-go]").forEach((b) => (b.onclick = () => show(b.dataset.go)));
$("#group-select").onchange = async (e) => { S.group = e.target.value; await loadFeed(); };
$("#role-select").onchange = async (e) => { S.role = e.target.value; await loadFeed(); };
$("#add-news-btn").onclick = () => $("#news-modal").classList.remove("hidden");
document.querySelectorAll("[data-close]").forEach((b) => (b.onclick = () => $("#" + b.dataset.close).classList.add("hidden")));
$("#n-submit").onclick = async () => {
  $("#n-result").innerHTML = '<div class="spinner">Извлекаем событие…</div>';
  const r = await api("/api/news", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user: S.user, title: $("#n-title").value, text: $("#n-text").value }) });
  const rel = r.relevance.find((x) => x.group_id === S.group) || r.relevance[0];
  $("#n-result").innerHTML = `<div class="note">Факторы: ${esc(JSON.stringify(r.event.factors))} · извлечение: ${esc(r.extractor)}<br>
    Значимость для группы: <b>${Math.round(Math.min(1, rel.score) * 100)}</b> ${rel.why.length ? "· почему: " + esc(rel.why.join("; ")) : ""}</div>
    <button class="primary" style="margin-top:8px" onclick="$('#news-modal').classList.add('hidden'); loadFeed(); openCard('${r.event.id}')">Открыть карточку</button>`;
};
start();
