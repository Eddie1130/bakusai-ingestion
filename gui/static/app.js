/* Local workspace. Forum content is always rendered as text, never as HTML. */
"use strict";
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const token = $('meta[name="workspace-token"]').content;
const state = {
  stores: [],
  regions: [],
  store: null,
  tab: "analysis",
  version: 0,
  generated: null,
  verified: null,
  report: "",
  offset: 0,
  browseVersion: 0,
  job: { status: "idle" },
  decision: null,
  finishedJob: null,
  connected: true,
  verifyVersion: 0,
};
const escapeHTML = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      })[c],
  );
const number = (value) => Number(value || 0).toLocaleString("zh-TW");
const dateOnly = (value) => (value ? String(value).slice(0, 10) : "日期不明");
const statusLabels = {
  known: "範圍內",
  missing: "不存在",
  outside_period: "期間外",
  not_sent: "未送出",
};
let toastTimer;

async function api(path, data) {
  let response;
  try {
    response = await fetch(path, {
      method: data === undefined ? "GET" : "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Workspace-Token": token,
      },
      body: data === undefined ? undefined : JSON.stringify(data),
      cache: "no-store",
    });
  } catch {
    throw new Error("無法連線至本機程式，請確認工作台仍在執行。");
  }
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "操作未完成，請稍後再試。");
  return result;
}
function toast(message) {
  $("#toast").textContent = message;
  $("#toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    $("#toast").hidden = true;
  }, 4000);
}
function showError(selector, error) {
  $(selector).textContent = error.message;
  $(selector).hidden = false;
}
async function busy(button, action) {
  const previous = button.innerHTML;
  button.disabled = true;
  button.textContent = "處理中…";
  try {
    return await action();
  } finally {
    button.disabled = false;
    button.innerHTML = previous;
  }
}
function context() {
  if (!state.store) throw new Error("請先選擇店家");
  return {
    acode: state.store.acode,
    store: state.store.name,
    start: $("#start").value,
    end: $("#end").value,
  };
}
function periodLabel() {
  return $("#start").value || $("#end").value
    ? `${$("#start").value || "最早"} ～ ${$("#end").value || "最新"}（含首尾日；排除日期不明）`
    : "全部已爬取資料（含日期不明留言）";
}
function updateScope() {
  $("#verify-period").textContent = periodLabel();
  $("#browse-scope").textContent =
    `目前期間：${periodLabel()} · 可在「分析提示詞」調整日期`;
}
function sameStore(a, b) {
  return a && b && a.acode === b.acode && a.name === b.name;
}
function renderStores() {
  const region = $("#region").value;
  const query = $("#store-search").value.trim().toLocaleLowerCase();
  const visible = state.stores.filter(
    (store) =>
      (region === "all" || String(store.acode) === region) &&
      store.name.toLocaleLowerCase().includes(query),
  );
  $("#store-count").textContent = state.stores.length;
  $("#stores").innerHTML = visible.length
    ? visible
        .map((store) => {
          const index = state.stores.indexOf(store);
          const active = sameStore(store, state.store);
          return `<button class="store-item${active ? " active" : ""}" data-store="${index}" ${active ? 'aria-current="true"' : ""}>
      <span class="store-monogram" aria-hidden="true">${escapeHTML([...store.name][0])}</span>
      <span><strong>${escapeHTML(store.name)}</strong><small>${escapeHTML(store.region)} · ${number(store.count)} 則${store.complete ? "" : " · 未完成"}</small></span></button>`;
        })
        .join("")
    : `<p class="muted small">${state.stores.length ? "沒有符合的店家，試試其他地區或名稱。" : "還沒有店家。從上方「新增店家」開始收集討論。"}</p>`;
}
async function loadStores(selectTarget) {
  const data = await api("/api/stores");
  state.stores = data.stores;
  state.regions = data.regions;
  if ($("#region").options.length === 1) {
    data.regions.forEach((region) =>
      $("#region").add(new Option(region.label, region.acode)),
    );
  }
  const wanted = selectTarget || state.store;
  const selected =
    data.stores.find((store) => sameStore(store, wanted)) ||
    (!state.store ? data.stores[0] : null);
  if (selected) {
    if (!sameStore(selected, state.store))
      chooseStore(selected, Boolean(selectTarget && state.store));
    else {
      state.store = selected;
      renderHeader();
    }
  } else if (!data.stores.length) {
    state.store = null;
    $("#welcome").hidden = false;
    $("#store-workspace").hidden = true;
  }
  renderStores();
}
function renderHeader() {
  const store = state.store;
  if (!store) return;
  $("#welcome").hidden = true;
  $("#store-workspace").hidden = false;
  $("#breadcrumb").textContent = store.name;
  $("#store-region").textContent = `${store.region} / 店家資料`;
  $("#store-title").textContent = store.name;
  let updated = "尚無更新時間";
  if (store.updated) {
    const parsed = new Date(store.updated);
    updated = Number.isNaN(parsed.getTime())
      ? store.updated
      : parsed.toLocaleString("zh-TW", { hour12: false });
  }
  $("#store-updated").textContent = `最後更新 ${updated}`;
  $("#stat-count").textContent = number(store.count);
  $("#stat-span").textContent =
    store.first || store.last
      ? `${dateOnly(store.first)} — ${dateOnly(store.last)}`
      : "尚無日期資料";
  $("#store-warning").hidden = store.complete && !store.error;
  $("#store-warning").textContent =
    store.error ||
    "上次抓取未完成。目前可使用已儲存資料，按「更新資料」繼續收集。";
}
function chooseStore(store, ask = true) {
  if (sameStore(store, state.store)) return;
  if (
    ask &&
    ($("#report").value.trim() || state.generated) &&
    !window.confirm("切換店家會清除目前畫面中的提示詞與報告。確定切換？")
  )
    return;
  state.store = store;
  state.version++;
  state.browseVersion++;
  state.generated = null;
  state.verified = null;
  $("#report").value = "";
  $("#target").value = "";
  $("#comment-search").value = "";
  $("#verification").hidden = true;
  $("#prompt-result").hidden = true;
  $("#analysis-error").hidden = true;
  $("#verify-error").hidden = true;
  $("#verify-scope").innerHTML =
    '<option value="store">目前店家與所選期間</option>';
  renderHeader();
  renderStores();
  updateScope();
  switchTab("analysis");
}
function switchTab(name) {
  state.tab = name;
  $$(".tab").forEach((button) => {
    const active = button.dataset.tab === name;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  ["analysis", "verify", "browse"].forEach((tab) => {
    $(`#${tab}-panel`).hidden = tab !== name;
  });
  updateScope();
  if (name === "browse" && state.store) loadComments(0);
}
function invalidate() {
  state.version++;
  state.generated = null;
  state.verified = null;
  $("#prompt-result").hidden = true;
  $("#verification").hidden = true;
  $("#verify-scope").innerHTML =
    '<option value="store">目前店家與所選期間</option>';
  updateScope();
}
function renderMode() {
  const mode = $('input[name="mode"]:checked').value;
  $("#target-field").hidden = mode === "roster";
  $("#target").required = mode !== "roster";
  $("#target-label").innerHTML =
    `${mode === "features" ? "特徵描述" : "女生名字"} <span class="required">必填</span>`;
  $("#target").placeholder =
    mode === "features"
      ? "例如：短髮、手臂有刺青，常穿白色洋裝…"
      : "輸入官網上的日文源氏名";
  $("#target-hint").textContent =
    mode === "features"
      ? "可以用中文描述；會提供期間內全部留言，讓模型比對日文線索。"
      : "保留原始日文寫法，比對會更準確。";
  $("#advanced").hidden = mode !== "name";
}
function localDate(date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}
function preset(months) {
  if (months === "all") {
    $("#start").value = "";
    $("#end").value = "";
  } else {
    const now = new Date();
    const start = new Date(
      now.getFullYear(),
      now.getMonth() - Number(months),
      1,
    );
    const lastDay = new Date(
      start.getFullYear(),
      start.getMonth() + 1,
      0,
    ).getDate();
    start.setDate(Math.min(now.getDate(), lastDay));
    $("#start").value = localDate(start);
    $("#end").value = "now";
  }
  $$("[data-period]").forEach((button) =>
    button.classList.toggle("selected", button.dataset.period === months),
  );
  invalidate();
}
function renderPrompt(result) {
  state.verifyVersion++;
  state.generated = result;
  const isFilter = result.stage === "filter";
  $("#prompt-result").innerHTML =
    `<div class="result-heading"><h3>${isFilter ? "第一輪提示詞已準備好" : "提示詞已準備好"}</h3><span class="badge">${isFilter ? "兩段式 · 1 / 2" : "可以複製了"}</span></div>
    <div class="result-metrics"><span><strong>${number(result.count)}</strong> 則留言</span><span><strong>${number(result.characters)}</strong> 字元</span><span>約 <strong>${number(result.tokens.lower)}～${number(result.tokens.upper)}</strong> tokens</span></div>
    <p class="field-hint">${escapeHTML(result.period)}</p>
    <p class="field-hint">tokens 為 UTF-8 位元組粗估，非精確計數，實際用量可能超出範圍；不含既有對話、系統指令或模型輸出。請確認模型能容納全文。</p>
    <div class="result-actions"><button class="button primary" id="copy-prompt">複製提示詞 <span aria-hidden="true">↗</span></button><button class="button secondary" id="download-prompt">下載文字檔</button>${isFilter ? "" : '<button class="text-button" id="go-verify">下一步：貼回模型報告 →</button>'}</div>
    <details><summary>預覽完整提示詞</summary><pre class="prompt-preview" id="prompt-text"></pre></details>
    ${result.notes.length ? `<details><summary>查看資料挑選說明</summary><div class="notice">${escapeHTML(result.notes.join("\n\n"))}</div></details>` : ""}
    ${isFilter ? '<div class="refine-section"><h3>接著，貼回模型選出的留言 ID</h3><p>將上方提示詞交給模型，再把第一輪完整回覆貼到這裡。</p><label class="sr-only" for="filter-answer">模型第一輪回覆</label><textarea id="filter-answer" rows="4" placeholder="例如：T1-123、T2-456…" maxlength="4000000"></textarea><button class="button primary" id="refine">產生第二輪提示詞 →</button><div id="refine-error" class="notice warning" role="alert" hidden></div></div>' : ""}`;
  $("#prompt-text").textContent = result.prompt;
  $("#prompt-result").hidden = false;
  $("#verification").hidden = true;
  state.verified = null;
  $("#verify-scope").innerHTML =
    '<option value="prompt">當次提示詞實際送出的留言（建議）</option><option value="store">目前店家與所選期間</option>';
  $("#prompt-result").scrollIntoView({ behavior: "smooth", block: "start" });
}
async function generate(event) {
  event.preventDefault();
  const version = state.version;
  $("#analysis-error").hidden = true;
  await busy($("#generate"), async () => {
    try {
      const mode = $('input[name="mode"]:checked').value;
      const result = await api("/api/generate", {
        ...context(),
        mode,
        target: $("#target").value,
        depth: $("#depth").value === "all" ? "all" : Number($("#depth").value),
        two_step: $("#two-step").checked,
      });
      if (version === state.version) renderPrompt(result);
    } catch (error) {
      if (version === state.version) showError("#analysis-error", error);
    }
  });
}
async function copyPrompt() {
  const generated = state.generated;
  if (!generated) return;
  try {
    await navigator.clipboard.writeText(generated.prompt);
    toast("已複製，貼給你慣用的模型即可。");
  } catch {
    const preview = $("#prompt-text");
    preview.parentElement.open = true;
    const range = document.createRange();
    range.selectNodeContents(preview);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    toast("無法自動複製，已選取提示詞。請按 ⌘C／Ctrl+C，或下載文字檔。");
  }
}
function downloadPrompt() {
  if (!state.generated) return;
  const url = URL.createObjectURL(
    new Blob([state.generated.prompt], { type: "text/plain;charset=utf-8" }),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = "bakusai-prompt.txt";
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
async function refine() {
  const version = state.version;
  const session = state.generated?.session;
  $("#refine-error").hidden = true;
  await busy($("#refine"), async () => {
    try {
      const result = await api("/api/refine", {
        ...context(),
        session,
        answer: $("#filter-answer").value,
      });
      if (version === state.version && state.generated?.session === session)
        renderPrompt(result);
    } catch (error) {
      if (version === state.version && $("#refine-error"))
        showError("#refine-error", error);
    }
  });
}
function citationButton(citation, active = false) {
  return `<button class="citation-link ${citation.status}${active ? " active" : ""}" data-citation="${escapeHTML(citation.id)}" aria-label="${escapeHTML(citation.id)}，${statusLabels[citation.status]}">${escapeHTML(citation.id)}</button>`;
}
function renderVerification(result, report) {
  state.verified = result;
  state.report = report;
  const issues = result.total - result.known;
  const byId = new Map(
    result.citations.map((citation) => [citation.id, citation]),
  );
  // Escape each text segment independently. Model output is untrusted plain text.
  const reading = report
    .split(/(T\d+-\d+)/g)
    .map((part) =>
      byId.has(part) ? citationButton(byId.get(part)) : escapeHTML(part),
    )
    .join("");
  $("#verification").innerHTML =
    `<div class="verification-summary"><div><h3>${result.total ? `已核對 ${number(result.total)} 個引用` : "沒有找到可核對的引用"}</h3><p>驗證範圍：${escapeHTML(result.scope)}</p></div><span class="badge${!result.total || issues ? " warning" : ""}">${!result.total ? "無法驗證" : issues ? `${issues} 個需注意 · ${result.missing} 個不存在` : "引用全部在範圍內"}</span></div>
    ${result.total ? "" : '<div class="notice warning">報告中沒有 T1-123 這類引用 ID，因此無法驗證。請讓模型保留引用後再貼回。</div>'}
    <div class="evidence-layout"><div class="report-reading" aria-label="報告全文">${reading}</div><aside class="evidence-panel" aria-label="引用原文"><h3>引用原文 <span class="muted small">／ 點選 ID 查看</span></h3><div class="citation-list">${result.citations.map((citation) => citationButton(citation)).join("")}</div><div id="evidence-detail"><p class="muted small">報告沒有引用，請補上留言 ID 後再核對。</p></div></aside></div>`;
  $("#verification").hidden = false;
  if (result.citations.length) selectCitation(result.citations[0].id);
  $("#verification").scrollIntoView({ behavior: "smooth", block: "start" });
}
function selectCitation(id) {
  const citation = state.verified?.citations.find((item) => item.id === id);
  if (!citation) return;
  $$(".citation-list .citation-link").forEach((button) => {
    const active = button.dataset.citation === id;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  const warning = {
    missing: "本機店家資料中沒有這個引用 ID。",
    outside_period: "這則留言存在，但在所選期間之外或日期不明。",
    not_sent: "這則留言存在，但沒有包含在當次提示詞中。",
  }[citation.status];
  $("#evidence-detail").innerHTML =
    `<div class="comment-meta"><strong>${escapeHTML(citation.id)}</strong><span>${escapeHTML(citation.date)}</span></div><span class="badge ${citation.status === "missing" ? "missing" : citation.status === "known" ? "" : "warning"}">${statusLabels[citation.status]}</span>${warning ? `<p class="notice warning">${warning}</p>` : ""}<p class="comment-body" id="evidence-body"></p><p class="comment-thread" id="evidence-thread"></p>`;
  $("#evidence-body").textContent = citation.body;
  $("#evidence-thread").textContent = citation.thread || "無討論串資料";
}
async function verify(event) {
  event.preventDefault();
  const version = state.version;
  const request = ++state.verifyVersion;
  $("#verify-error").hidden = true;
  const report = $("#report").value;
  await busy($("#verify-button"), async () => {
    try {
      const result = await api("/api/verify", {
        ...context(),
        report,
        session:
          $("#verify-scope").value === "prompt" ? state.generated?.session : "",
      });
      if (
        version === state.version &&
        request === state.verifyVersion &&
        report === $("#report").value
      )
        renderVerification(result, report);
    } catch (error) {
      if (version === state.version && request === state.verifyVersion)
        showError("#verify-error", error);
    }
  });
}
async function loadComments(offset = 0) {
  const version = state.version;
  const request = ++state.browseVersion;
  state.offset = offset;
  $("#comments").innerHTML =
    '<p class="empty-state"><span class="spinner" aria-hidden="true"></span>正在讀取留言…</p>';
  $("#pagination").innerHTML = "";
  try {
    const result = await api("/api/browse", {
      ...context(),
      query: $("#comment-search").value,
      offset,
    });
    if (version !== state.version || request !== state.browseVersion) return;
    $("#comments").innerHTML = result.comments.length
      ? result.comments
          .map(
            (comment) =>
              `<article class="comment-card"><div class="comment-meta"><strong>${escapeHTML(comment.id)}</strong><span>${escapeHTML(comment.date)}</span></div><p class="comment-body">${escapeHTML(comment.body)}</p><p class="comment-thread">${escapeHTML(comment.thread)}</p></article>`,
          )
          .join("")
      : '<div class="empty-state"><strong>這裡還沒有符合的留言。</strong>試試其他關鍵字，或到分析頁調整日期。</div>';
    $("#pagination").innerHTML =
      `<button class="button secondary" data-page="${Math.max(0, offset - 40)}" ${offset === 0 ? "disabled" : ""}>← 上一頁</button><span>${result.total ? `${number(offset + 1)}–${number(Math.min(offset + 40, result.total))} / ` : ""}${number(result.total)} 則</span><button class="button secondary" data-page="${offset + 40}" ${offset + 40 >= result.total ? "disabled" : ""}>下一頁 →</button>`;
  } catch (error) {
    if (version === state.version && request === state.browseVersion)
      $("#comments").innerHTML =
        `<p class="notice warning" role="alert">${escapeHTML(error.message)}</p>`;
  }
}
function activeJob() {
  return ["running", "waiting"].includes(state.job.status);
}
function openScrape(existing = false) {
  const dialog = $("#scrape-dialog");
  state.decision = null;
  if (activeJob()) renderJob();
  else {
    $("#scrape-title").textContent = existing ? "更新店家資料" : "新增店家";
    const region = existing
      ? state.store.acode
      : $("#region").value === "all"
        ? state.regions[0]?.acode
        : Number($("#region").value);
    $("#scrape-content").innerHTML =
      `<form id="scrape-form" class="dialog-form"><div class="field"><label for="scrape-region">地區</label><select id="scrape-region" ${existing ? "disabled" : ""}>${state.regions.map((item) => `<option value="${item.acode}" ${item.acode === region ? "selected" : ""}>${escapeHTML(item.label)}</option>`).join("")}</select></div><div class="field"><label for="scrape-name">店家名稱</label><input id="scrape-name" required maxlength="200" placeholder="例如：渋谷 マテリアル" value="${existing ? escapeHTML(state.store.name) : ""}" ${existing ? "readonly" : ""}><p class="field-hint">${existing ? "會保留已有歷史資料與引用 ID，合併新增留言。" : "同名店可在店名前加上小區，別名可寫成 嵐(錦)。"}</p></div><label class="checkbox-label"><input id="scrape-full" type="checkbox"><span>抓取全部歷史<small>預設抓取近 24 個月；全部歷史可能需要更長時間。</small></span></label><p>先搜尋並查看計畫，確認後才開始抓取留言。請求間隔維持至少 2 秒。</p><div id="scrape-error" class="notice warning" role="alert" hidden></div><button id="scrape-start" class="button primary" type="submit">搜尋並預覽計畫 →</button></form>`;
    $("#scrape-form").addEventListener("submit", (event) =>
      startScrape(event, existing),
    );
  }
  if (!dialog.open) dialog.showModal();
}
async function startScrape(event, existing) {
  event.preventDefault();
  $("#scrape-error").hidden = true;
  await busy($("#scrape-start"), async () => {
    try {
      state.job = await api("/api/scrape", {
        acode: Number($("#scrape-region").value),
        store: $("#scrape-name").value,
        existing,
        full: $("#scrape-full").checked,
      });
      state.decision = null;
      renderJob();
      renderJobIndicator();
    } catch (error) {
      if ($("#scrape-error")) showError("#scrape-error", error);
      else toast(error.message);
    }
  });
}
function renderJobIndicator() {
  $("#job-indicator").hidden = state.job.status === "idle";
  $("#job-indicator").textContent = activeJob()
    ? state.job.status === "waiting"
      ? "抓取需要你的確認"
      : "抓取進行中"
    : "查看抓取結果";
  $("#refresh-store").disabled = activeJob();
}
function renderJob() {
  if (!$("#scrape-dialog").open && !$("#scrape-form")) return;
  const job = state.job;
  if (job.status === "idle") return;
  $("#scrape-title").textContent = job.store || "收集討論";
  const decisionKey = job.decision?.id || `${job.id}:${job.status}`;
  if (state.decision === decisionKey) {
    const message = $("#job-message");
    if (message) message.textContent = job.message;
    const progress = $("#job-progress");
    if (progress) {
      progress.max = Math.max(job.total, 1);
      progress.value = job.done;
    }
    const counter = $("#job-counter");
    if (counter)
      counter.textContent = job.total
        ? `${job.done} / 約 ${job.total} 頁`
        : "搜尋與等待會依網站回應調整，請稍候。";
    return;
  }
  state.decision = decisionKey;
  const decision = job.decision;
  let body = `<div class="job-state"><h3>${activeJob() ? '<span class="spinner" aria-hidden="true"></span>' : ""}${escapeHTML(decision?.title || (job.status === "done" ? "收集完成。" : job.status === "error" ? "這次沒有完成抓取" : job.status === "cancelled" ? "已停止抓取" : job.phase))}</h3><p id="job-message" class="job-message" role="status">${escapeHTML(job.message)}</p>`;
  if (decision?.kind === "candidate") {
    body += decision.options
      .map(
        (option, index) =>
          `<div class="candidate"><button data-choice="${index}">${escapeHTML(option.name)} →</button><p>${number(option.count)} 串 · ${escapeHTML(option.boards.join("、"))}</p><details><summary>檢視討論串標題</summary><pre>${escapeHTML(option.threads.join("\n"))}</pre></details></div>`,
      )
      .join("");
  } else if (decision) {
    body += `<pre>${escapeHTML(decision.summary)}</pre>${decision.detail ? `<details><summary>檢視計畫明細</summary><pre>${escapeHTML(decision.detail)}</pre></details>` : ""}`;
  } else if (activeJob()) {
    body += `<progress id="job-progress" class="job-progress" max="${Math.max(job.total, 1)}" ${job.total ? `value="${job.done}"` : ""} aria-label="抓取頁數進度"></progress><p id="job-counter">${job.total ? `${job.done} / 約 ${job.total} 頁` : "搜尋與等待會依網站回應調整，請稍候。"}</p>`;
  }
  body += `<div class="job-actions">${activeJob() ? `<button class="button secondary" id="cancel-job">停止抓取</button>${decision && decision.kind !== "candidate" ? '<button class="button primary" id="confirm-job">確認並繼續 →</button>' : '<button class="button secondary" id="background-job">在背景繼續</button>'}` : '<button class="button primary" id="finish-job">完成</button>'}</div></div>`;
  $("#scrape-content").innerHTML = body;
}
async function jobAction(action, value) {
  const buttons = $$("#scrape-content button");
  buttons.forEach((button) => {
    button.disabled = true;
  });
  try {
    state.job = await api("/api/job", {
      id: state.job.id,
      action,
      decision: state.job.decision?.id,
      value,
    });
    // The next poll will show the actual checkpoint after the worker wakes up.
    if (action === "cancel") toast("已要求停止，會在下一個安全檢查點結束。");
    else toast("已確認，正在繼續處理。");
  } catch (error) {
    toast(error.message);
  } finally {
    buttons.forEach((button) => {
      button.disabled = false;
    });
  }
}
async function pollJob() {
  try {
    state.job = await api("/api/job");
    state.connected = true;
    $("#connection-banner").hidden = true;
    renderJobIndicator();
    if ($("#scrape-dialog").open && !$("#scrape-form")) renderJob();
    if (state.job.id && !activeJob() && state.finishedJob !== state.job.id) {
      state.finishedJob = state.job.id;
      await loadStores();
      if (state.job.status === "done") toast("店家資料已更新。");
    }
  } catch {
    state.connected = false;
    $("#connection-banner").hidden = false;
  }
  setTimeout(pollJob, 1200);
}

$("#region").addEventListener("change", renderStores);
$("#store-search").addEventListener("input", renderStores);
$("#stores").addEventListener("click", (event) => {
  const button = event.target.closest("[data-store]");
  if (button) chooseStore(state.stores[Number(button.dataset.store)]);
});
$$("[data-tab]").forEach((button) =>
  button.addEventListener("click", () => switchTab(button.dataset.tab)),
);
$$("[data-period]").forEach((button) =>
  button.addEventListener("click", () => preset(button.dataset.period)),
);
$$('input[name="mode"]').forEach((input) =>
  input.addEventListener("change", () => {
    renderMode();
    invalidate();
  }),
);
["target", "start", "end", "depth", "two-step"].forEach((id) =>
  $(`#${id}`).addEventListener("input", () => {
    if (id === "start" || id === "end")
      $$("[data-period]").forEach((button) =>
        button.classList.remove("selected"),
      );
    invalidate();
  }),
);
$("#analysis-form").addEventListener("submit", generate);
$("#prompt-result").addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  if (button.id === "copy-prompt") copyPrompt();
  if (button.id === "download-prompt") downloadPrompt();
  if (button.id === "refine") refine();
  if (button.id === "go-verify") {
    switchTab("verify");
    $("#report").focus();
  }
});
$("#verify-form").addEventListener("submit", verify);
$("#report").addEventListener("input", () => {
  state.verifyVersion++;
  $("#verification").hidden = true;
  state.verified = null;
});
$("#verify-scope").addEventListener("change", () => {
  state.verifyVersion++;
  $("#verification").hidden = true;
  state.verified = null;
});
$("#verification").addEventListener("click", (event) => {
  const button = event.target.closest("[data-citation]");
  if (button) selectCitation(button.dataset.citation);
});
$("#browse-form").addEventListener("submit", (event) => {
  event.preventDefault();
  loadComments(0);
});
$("#pagination").addEventListener("click", (event) => {
  const button = event.target.closest("[data-page]");
  if (button) loadComments(Number(button.dataset.page));
});
$("#add-store").addEventListener("click", () => openScrape());
$("#welcome-add").addEventListener("click", () => openScrape());
$("#refresh-store").addEventListener("click", () => openScrape(true));
$("#job-indicator").addEventListener("click", () => {
  state.decision = null;
  $("#scrape-content").innerHTML = "";
  $("#scrape-dialog").showModal();
  renderJob();
});
$("#close-scrape").addEventListener("click", () => $("#scrape-dialog").close());
$("#scrape-content").addEventListener("click", async (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  if (button.dataset.choice !== undefined)
    jobAction("answer", Number(button.dataset.choice));
  if (button.id === "confirm-job") jobAction("answer", true);
  if (button.id === "cancel-job") jobAction("cancel");
  if (button.id === "background-job") $("#scrape-dialog").close();
  if (button.id === "finish-job") {
    $("#scrape-dialog").close();
    if (state.job.status === "done") {
      try {
        await loadStores({ acode: state.job.acode, name: state.job.store });
      } catch (error) {
        toast(error.message);
      }
    }
  }
});
$("#help").addEventListener("click", () => $("#help-dialog").showModal());
$("#close-help").addEventListener("click", () => $("#help-dialog").close());
renderMode();
updateScope();
loadStores().catch((error) => {
  $("#stores").innerHTML =
    `<p class="notice warning">${escapeHTML(error.message)}</p><button class="button secondary" id="retry-stores">重新載入</button>`;
  $("#stores").addEventListener("click", (event) => {
    if (event.target.id === "retry-stores")
      loadStores().catch((retryError) => toast(retryError.message));
  });
});
pollJob();
