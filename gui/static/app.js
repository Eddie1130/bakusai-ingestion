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
  templateCatalog: null,
  templateOverrides: {},
  templatePromise: null,
  templateWarning: "",
  templateVersion: 0,
  customPreview: null,
  customIds: new Set(),
  customSelectionMode: "exclude",
  customThreads: null,
  customThreadOptions: [],
  customThreadRequest: 0,
  customVersion: 0,
  customSeed: "",
  generating: false,
  fullRun: null,
  fullDrafts: new Map(),
  fullRequest: 0,
  fullBusy: false,
};
const TEMPLATE_STORAGE_KEY = "bakusai.prompt-templates.v1";
const FULL_RUN_KEY = "bakusai.full-filter.v1";
try {
  const saved = JSON.parse(sessionStorage.getItem(FULL_RUN_KEY));
  if (
    saved &&
    typeof saved.session === "string" &&
    Number.isInteger(saved.acode) &&
    typeof saved.name === "string"
  )
    state.fullRun = saved;
} catch {
  /* Tab storage is optional; server-side progress remains available. */
}
const templateEditor = { drafts: {}, current: null };
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
  captureFullDraft();
  state.store = store;
  state.customThreads = null;
  state.customThreadOptions = [];
  renderCustomThreads();
  resetCustomSelection();
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
  renderMode();
  switchTab("analysis");
  updateFullResume();
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
  captureFullDraft();
  state.fullRequest++;
  state.version++;
  state.generated = null;
  state.verified = null;
  $("#prompt-result").hidden = true;
  $("#verification").hidden = true;
  $("#verify-scope").innerHTML =
    '<option value="store">目前店家與所選期間</option>';
  updateScope();
  updateCustomCount();
  updateFullResume();
}
function renderMode() {
  const mode = $('input[name="mode"]:checked').value;
  const other = mode === "other";
  $("#target-field").hidden = !["name", "features"].includes(mode);
  $("#target").required = ["name", "features"].includes(mode);
  $("#custom-task").hidden = !other;
  $("#custom-controls").hidden = !other;
  $("#custom-instructions").required = other;
  $("#custom-instructions").disabled = !other;
  $$("#custom-controls input, #custom-controls select").forEach((input) => {
    input.disabled = !other;
  });
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
  $("#advanced").hidden = !["name", "features"].includes(mode);
  $("#advanced-caption").textContent =
    mode === "features" ? "分析方式與分批" : "回覆脈絡與兩段式分析";
  updateFilterOptions();
  if (other) {
    seedCustomInstructions();
    loadCustomThreads();
  }
  updateCustomCount();
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
  resetCustomSelection();
  if ($('input[name="mode"]:checked').value === "other") loadCustomThreads();
}
function renderPrompt(result) {
  if (result.stage === "full_filter") {
    renderFullFilter(result);
    return;
  }
  state.verifyVersion++;
  state.generated = result;
  const isFilter = result.stage === "filter";
  $("#prompt-result").innerHTML =
    `<div class="result-heading"><h3>${isFilter ? "第一輪提示詞已準備好" : "提示詞已準備好"}</h3><span class="badge">${isFilter ? "兩段式 · 1 / 2" : "可以複製了"}</span></div>
    <div class="result-metrics"><span><strong>${number(result.count)}</strong> 則留言</span><span><strong>${number(result.characters)}</strong> 字元</span><span>約 <strong>${number(result.tokens.lower)}～${number(result.tokens.upper)}</strong> tokens</span></div>
    <p class="field-hint">${escapeHTML(result.period)}</p>
    ${result.warning ? `<div class="notice warning">${escapeHTML(result.warning)}</div>` : ""}
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
  updateFullResume();
}
async function generate(event) {
  event.preventDefault();
  const version = state.version;
  const templateVersion = state.templateVersion;
  $("#analysis-error").hidden = true;
  state.generating = true;
  state.fullRequest++;
  await busy($("#generate"), async () => {
    try {
      await loadTemplates();
      if (
        version !== state.version ||
        templateVersion !== state.templateVersion
      )
        return;
      const mode = $('input[name="mode"]:checked').value;
      if (mode === "other" && !state.customPreview)
        throw new Error("請先預覽並勾選要提供的留言");
      const result = await api("/api/generate", {
        ...context(),
        mode,
        target: $("#target").value,
        depth: $("#depth").value === "all" ? "all" : Number($("#depth").value),
        two_step: $("#two-step").checked,
        filter_strategy: $("#filter-strategy").value,
        feature_analysis: $("#feature-analysis").value,
        batch_comments: Number($("#batch-comments").value),
        templates: state.templateOverrides,
        ...(mode === "other"
          ? {
              instructions: $("#custom-instructions").value,
              selection_session: state.customPreview.session,
              selection_mode: state.customSelectionMode,
              comment_ids: [...state.customIds],
            }
          : {}),
      });
      if (
        version === state.version &&
        templateVersion === state.templateVersion
      )
        renderPrompt(result);
    } catch (error) {
      if (
        version === state.version &&
        templateVersion === state.templateVersion
      )
        showError("#analysis-error", error);
    }
  });
  state.generating = false;
  updateCustomCount();
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
  link.download =
    state.generated.stage === "full_filter"
      ? `bakusai-filter-${state.generated.filter.current + 1}-of-${state.generated.filter.total}.txt`
      : "bakusai-prompt.txt";
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
async function refine() {
  if (state.generated?.stage === "full_filter") {
    await submitFullBatch();
    return;
  }
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
function updateFilterOptions() {
  const mode = $('input[name="mode"]:checked').value;
  const features = mode === "features";
  const nameEnabled = mode === "name" && $("#two-step").checked;
  const full =
    features || (nameEnabled && $("#filter-strategy").value === "full");
  $("#name-analysis-options").hidden = mode !== "name";
  $("#two-step").disabled = mode !== "name";
  $("#feature-analysis-field").hidden = !features;
  $("#feature-analysis").disabled = !features;
  $("#feature-analysis-hint").textContent =
    $("#feature-analysis").value === "direct"
      ? "一批內可直接完成；超過一批會逐批收集暫時分析，最後整合重複人選、別名與矛盾。每項具體說法需附本批引用 ID。"
      : "全部期間內留言分批閱讀，先選取符合、部分符合、矛盾與不確定線索的 ID，再合併原文產生候選人報告。";
  $("#two-step-options").hidden = !nameEnabled && !features;
  $("#filter-strategy-field").hidden = features;
  $("#filter-strategy").disabled = !nameEnabled;
  $("#batch-size-field").hidden = !full;
  $("#batch-comments").disabled = !full;
  $("#depth").disabled = full || mode !== "name";
  $("#filter-strategy-hint").textContent = full
    ? "全部期間內留言都會閱讀，不先比對名字。依留言數量分批，搭配同串鄰近及直接回覆脈絡；第二輪固定展開一層回覆。"
    : "提供名字比對及回覆脈絡挑出的候選留言，讓模型排除誤判。";
}
function updateFullResume() {
  const run = state.fullRun;
  $("#full-resume").hidden = !run || state.generated?.session === run.session;
  if (run)
    $("#full-resume").textContent =
      `繼續${run.analysis_mode === "features" ? "特徵尋人分批處理" : "完整篩選"}：${run.name}・${run.completed || 0}/${run.total || "?"} 批已完成`;
}
function fullDraftKey(result = state.generated) {
  return `${result.session}:${result.filter.current}`;
}
function captureFullDraft() {
  if (state.generated?.stage !== "full_filter" || !$("#filter-answer")) return;
  const value = $("#filter-answer").value;
  const key = fullDraftKey();
  if (value.trim() === state.generated.filter.saved_answer.trim())
    state.fullDrafts.delete(key);
  else state.fullDrafts.set(key, value);
}
function hasFullDrafts() {
  const prefix = `${state.generated?.session}:`;
  return [...state.fullDrafts.keys()].some((key) => key.startsWith(prefix));
}
function batchOptionLabel(batch, result) {
  const saved =
    result.output_kind === "reports"
      ? `已儲存分析${batch.missing_citations ? "・無引用" : ""}`
      : `已確認 ${batch.confirmed_count} 則`;
  const status = state.fullDrafts.has(`${result.session}:${batch.index}`)
    ? "有未確認草稿"
    : batch.complete
      ? saved
      : "待處理";
  return `第 ${batch.index + 1} 批・${batch.primary_count} 則主要留言・${status}`;
}
function updateFullControls() {
  if (state.generated?.stage !== "full_filter") return;
  const filter = state.generated.filter;
  if ($("#filter-answer")) $("#filter-answer").disabled = state.fullBusy;
  if ($("#full-discard")) $("#full-discard").disabled = state.fullBusy;
  if ($("#full-batch-select"))
    [...$("#full-batch-select").options].forEach((option) => {
      const batch = filter.batches[Number(option.value)];
      option.textContent = batchOptionLabel(batch, state.generated);
    });
  $$("[data-full-nav]").forEach((button) => {
    const i = Number(button.dataset.fullNav);
    button.disabled = state.fullBusy || i < 0 || i >= filter.total;
  });
  if ($("#full-batch-select"))
    $("#full-batch-select").disabled = state.fullBusy;
  if ($("#refine")) $("#refine").disabled = state.fullBusy;
  if ($("#full-finalize"))
    $("#full-finalize").disabled =
      state.fullBusy ||
      !filter.all_done ||
      (!filter.confirmed_count && state.generated.output_kind !== "reports") ||
      hasFullDrafts();
  if ($("#full-unsaved")) $("#full-unsaved").hidden = !hasFullDrafts();
}
function renderFullFilter(result) {
  state.generated = result;
  state.verifyVersion++;
  state.verified = null;
  $("#verification").hidden = true;
  $("#verify-scope").innerHTML =
    '<option value="store">目前店家與所選期間</option>';
  const f = result.filter;
  const direct = result.output_kind === "reports";
  const features = result.analysis_mode === "features";
  const title = direct
    ? "特徵尋人・逐批分析"
    : features
      ? "特徵尋人・篩選留言"
      : "完整篩選";
  state.fullRun = {
    session: result.session,
    acode: result.acode,
    name: result.store,
    target: result.target,
    start: result.start,
    end: result.end,
    current: f.current,
    completed: f.completed,
    total: f.total,
    comment_limit: f.comment_limit,
    analysis_mode: result.analysis_mode || "name",
    feature_analysis: result.output_kind === "reports" ? "direct" : "filter",
  };
  try {
    sessionStorage.setItem(FULL_RUN_KEY, JSON.stringify(state.fullRun));
  } catch {
    /* Progress still lives in memory. */
  }
  $("#prompt-result").innerHTML =
    `<div class="result-heading"><h3>${title}・第 ${f.current + 1} / ${f.total} 批</h3><span class="badge">第一輪・已完成 ${f.completed} 批</span></div>
    <progress class="full-progress" value="${f.completed}" max="${f.total}" aria-label="分批處理完成批次"></progress>
    <div class="result-metrics"><span>全部 <strong>${number(f.total_comments)}</strong> 則</span><span>已閱讀主要留言 <strong>${number(f.reviewed_count)}</strong> 則</span><span>${direct ? "去重後引用證據" : "去重後確認"} <strong>${number(f.confirmed_count)}</strong> 則</span></div>
    <p class="field-hint">${escapeHTML(result.period)}・每批最多 ${number(f.comment_limit)} 則留言（含補充脈絡）。</p>
    <p class="field-hint">全部批次合計約 ${number(f.total_tokens.lower)}～${number(f.total_tokens.upper)} tokens，包含重複脈絡。這是 UTF-8 位元組粗估，實際用量可能超出範圍，不含對話歷史或模型回覆；每批建議使用新對話。</p>
    <div class="full-batch-toolbar"><button class="button secondary" data-full-nav="${f.current - 1}">← 上一批</button><label class="sr-only" for="full-batch-select">選擇處理批次</label><select id="full-batch-select">${f.batches.map((batch) => `<option value="${batch.index}" ${batch.index === f.current ? "selected" : ""}>${escapeHTML(batchOptionLabel(batch, result))}</option>`).join("")}</select><button class="button secondary" data-full-nav="${f.current + 1}">下一批 →</button></div>
    <div class="notice">本批：${number(f.primary_ids.length)} 則主要留言＋${number(f.context_ids.length)} 則補充脈絡・${number(result.characters)} 字元・約 ${number(result.tokens.lower)}～${number(result.tokens.upper)} tokens。</div>
    <div class="result-actions"><button class="button primary" id="copy-prompt">複製本批提示詞 ↗</button><button class="button secondary" id="download-prompt">下載本批文字檔</button></div>
    <details><summary>預覽本批完整提示詞</summary><pre class="prompt-preview" id="prompt-text"></pre></details>
    ${result.notes.length > 1 ? `<p class="notice warning">${escapeHTML(result.notes.slice(1).join("\n"))}</p>` : ""}
    <div class="refine-section"><h3>貼回第 ${f.current + 1} 批的${direct ? "暫時分析" : " ID 陣列"}</h3><p>${direct ? "貼上本批完整的候選人分析，具體說法需引用本批提供的 ID；沒有相關人選時請明說。" : "只接受本批實際提供的 ID。沒有相關留言時貼上 []，也需要確認此批。"}</p><label class="sr-only" for="filter-answer">本批模型回覆</label><textarea id="filter-answer" rows="4" maxlength="4000000" placeholder="${direct ? "貼上本批模型的完整分析與引用 ID…" : "貼上 ID 陣列；沒有相關留言填 []"}"></textarea>
    <div class="result-actions"><button class="button primary" id="refine">${f.batches[f.current].complete ? "更新本批結果" : direct ? "儲存本批分析，繼續處理" : "確認本批，繼續處理"}</button><button class="text-button" id="full-discard">捨棄本批草稿</button><button class="text-button" data-full-nav="${f.current}">重新載入本批</button></div><div id="refine-error" class="notice warning" role="alert" hidden></div></div>
    <p id="full-unsaved" class="notice warning" hidden>仍有未確認的回覆修改。請先確認各批結果，或捨棄草稿，再產生最後的整合或報告提示詞。</p>
    ${f.all_done ? `<div class="notice">${direct ? "所有批次分析已收集，可以產生跨批整合提示詞，統整候選人、別名與矛盾。" : f.confirmed_count ? "所有批次已完成，可以產生第二輪報告提示詞。" : "所有批次已完成，但沒有確認相關留言。可回到各批修正結果，或調整日期與分析條件重新分析。"}</div>` : `<p class="field-hint">${direct ? "完成所有批次分析後，才會開放跨批整合提示詞。" : "完成所有批次後，才會開放產生第二輪報告提示詞。"}</p>`}
    <button id="full-finalize" class="button primary">${direct ? "產生跨批整合提示詞 →" : features ? "產生候選人報告提示詞 →" : "產生第二輪報告提示詞 →"}</button>`;
  $("#prompt-text").textContent = result.prompt;
  $("#filter-answer").value =
    state.fullDrafts.get(fullDraftKey(result)) ?? f.saved_answer;
  $("#prompt-result").hidden = false;
  updateFullControls();
  updateFullResume();
}
async function fullNavigate(index) {
  if (state.fullBusy || state.generated?.stage !== "full_filter") return;
  captureFullDraft();
  const session = state.generated.session;
  const version = state.version;
  const request = ++state.fullRequest;
  state.fullBusy = true;
  updateFullControls();
  try {
    const result = await api("/api/filter-batch", {
      ...context(),
      session,
      batch_index: index,
    });
    if (
      version === state.version &&
      request === state.fullRequest &&
      state.generated?.session === session
    )
      renderFullFilter(result);
  } catch (error) {
    if (
      version === state.version &&
      request === state.fullRequest &&
      $("#refine-error")
    )
      showError("#refine-error", error);
  } finally {
    state.fullBusy = false;
    updateFullControls();
  }
}
async function submitFullBatch() {
  if (state.fullBusy) return;
  captureFullDraft();
  const current = state.generated;
  const submitted = $("#filter-answer").value;
  const version = state.version;
  const request = ++state.fullRequest;
  state.fullBusy = true;
  updateFullControls();
  $("#refine-error").hidden = true;
  try {
    const result = await api("/api/refine", {
      ...context(),
      session: current.session,
      answer: submitted,
      batch_index: current.filter.current,
      filter_revision: current.filter.revision,
    });
    if (state.fullDrafts.get(fullDraftKey(current)) === submitted)
      state.fullDrafts.delete(fullDraftKey(current));
    if (
      version === state.version &&
      request === state.fullRequest &&
      state.generated?.session === current.session
    ) {
      renderFullFilter(result);
    }
  } catch (error) {
    if (
      version === state.version &&
      request === state.fullRequest &&
      $("#refine-error")
    )
      showError("#refine-error", error);
  } finally {
    state.fullBusy = false;
    updateFullControls();
  }
}
async function finalizeFullFilter() {
  if (state.fullBusy || state.generated?.stage !== "full_filter") return;
  captureFullDraft();
  updateFullControls();
  if (hasFullDrafts()) return;
  const current = state.generated;
  const version = state.version;
  const request = ++state.fullRequest;
  state.fullBusy = true;
  updateFullControls();
  try {
    const result = await api("/api/filter-report", {
      ...context(),
      session: current.session,
      filter_revision: current.filter.revision,
    });
    if (
      version === state.version &&
      request === state.fullRequest &&
      state.generated?.session === current.session
    )
      renderPrompt(result);
  } catch (error) {
    if (
      version === state.version &&
      request === state.fullRequest &&
      $("#refine-error")
    )
      showError("#refine-error", error);
  } finally {
    state.fullBusy = false;
    updateFullControls();
  }
}
async function resumeFullFilter() {
  const run = state.fullRun;
  if (!run) return;
  const wanted = state.stores.find(
    (store) => store.acode === run.acode && store.name === run.name,
  );
  if (!wanted) {
    toast("找不到上次完整篩選的店家，請先載入店家資料。");
    return;
  }
  chooseStore(wanted);
  if (!sameStore(wanted, state.store)) return;
  captureFullDraft();
  state.version++;
  const request = ++state.fullRequest;
  const version = state.version;
  $("#target").value = run.target;
  $("#start").value = run.start;
  $("#end").value = run.end;
  const mode = run.analysis_mode === "features" ? "features" : "name";
  $(`input[name="mode"][value="${mode}"]`).checked = true;
  $("#feature-analysis").value = run.feature_analysis || "direct";
  $("#two-step").checked = true;
  $("#filter-strategy").value = "full";
  $("#batch-comments").value = run.comment_limit ?? 200;
  $("#advanced").open = true;
  renderMode();
  switchTab("analysis");
  try {
    const result = await api("/api/filter-batch", {
      ...context(),
      session: run.session,
      batch_index: run.current,
    });
    if (version === state.version && request === state.fullRequest) {
      renderFullFilter(result);
      $("#prompt-result").scrollIntoView({
        behavior: "smooth",
        block: "start",
      });
    }
  } catch (error) {
    if (version === state.version && request === state.fullRequest) {
      showError("#analysis-error", error);
      // Keep the resume handle on network errors; expired handles can be replaced
      // by starting a new full filtering run.
    }
  }
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

function syncCustomSeed() {
  const template = state.templateCatalog?.find(
    (item) => item.name === "custom_spec.md",
  );
  if (!template) return;
  const seed = state.templateOverrides[template.name] ?? template.default;
  const input = $("#custom-instructions");
  if (!input.value || input.value === state.customSeed) input.value = seed;
  state.customSeed = seed;
}
async function seedCustomInstructions() {
  try {
    await loadTemplates();
    syncCustomSeed();
  } catch (error) {
    showError("#analysis-error", error);
  }
}
function resetCustomSelection() {
  state.customVersion++;
  state.customPreview = null;
  state.customIds = new Set();
  state.customSelectionMode = "exclude";
  $("#custom-preview").hidden = true;
  $("#custom-preview-error").hidden = true;
  $("#custom-selection-hint").textContent =
    "先預覽留言，再產生提示詞。修改日期或篩選條件後需重新預覽。";
  updateCustomCount();
}
function customSelectedCount() {
  return state.customPreview
    ? state.customSelectionMode === "include"
      ? state.customIds.size
      : state.customPreview.count - state.customIds.size
    : 0;
}
function updateCustomCount() {
  const count = customSelectedCount();
  if (state.customPreview)
    $("#custom-count").textContent =
      `符合 ${number(state.customPreview.matched)} 則・本批 ${number(state.customPreview.count)} 則・已選 ${number(count)} 則`;
  $("#generate").disabled =
    state.generating ||
    ($('input[name="mode"]:checked').value === "other" && count === 0);
}
function renderCustomThreads() {
  const query = $("#custom-thread-search").value.trim().toLocaleLowerCase();
  $("#custom-threads").innerHTML =
    state.customThreadOptions
      .map((thread, index) => {
        const matches = `${thread.id} ${thread.title}`
          .toLocaleLowerCase()
          .includes(query);
        const checked =
          state.customThreads === null || state.customThreads.has(thread.id);
        return `<label class="custom-thread-option" ${matches ? "" : "hidden"}><input type="checkbox" data-thread="${index}" ${checked ? "checked" : ""}><span>${escapeHTML(thread.id)} · ${escapeHTML(thread.title)}<small>${number(thread.count)} 則期間內留言</small></span></label>`;
      })
      .join("") || '<p class="field-hint">目前沒有可用討論串。</p>';
  const selected = state.customThreadOptions.filter(
    (t) => state.customThreads === null || state.customThreads.has(t.id),
  ).length;
  $("#custom-thread-summary").textContent =
    `已選 ${selected} / ${state.customThreadOptions.length} 串`;
}
async function loadCustomThreads() {
  if (!state.store || $('input[name="mode"]:checked').value !== "other") return;
  const request = ++state.customThreadRequest;
  const source = JSON.stringify(context());
  $("#custom-thread-error").hidden = true;
  $("#custom-preview-button").disabled = true;
  try {
    const result = await api("/api/custom-threads", context());
    if (
      request !== state.customThreadRequest ||
      source !== JSON.stringify(context())
    )
      return;
    state.customThreadOptions = result.threads;
    if ($('input[name="mode"]:checked').value !== "other") return;
    renderCustomThreads();
    $("#custom-preview-button").disabled = false;
  } catch (error) {
    if (request === state.customThreadRequest)
      showError("#custom-thread-error", error);
  }
}
async function previewCustomComments() {
  const version = state.customVersion;
  const limit = $("#custom-limit").value;
  $("#custom-preview-error").hidden = true;
  if (!/^\d+$/.test(limit) || Number(limit) > 1000000) {
    showError(
      "#custom-preview-error",
      new Error("留言數量上限必須為 0～1,000,000 的整數"),
    );
    return;
  }
  await busy($("#custom-preview-button"), async () => {
    try {
      const result = await api("/api/custom-preview", {
        ...context(),
        threads: state.customThreads === null ? null : [...state.customThreads],
        ranges: $("#custom-ranges").value,
        limit: Number(limit),
        order: $("#custom-order").value,
      });
      if (version !== state.customVersion) return;
      invalidate();
      state.customPreview = {
        session: result.session,
        count: result.count,
        matched: result.matched,
      };
      state.customIds = new Set();
      state.customSelectionMode = "exclude";
      $("#custom-preview").hidden = false;
      $("#custom-selection-hint").textContent = result.count
        ? "確認勾選後，即可產生提示詞。"
        : "沒有符合的留言，請調整範圍後重新預覽。";
      renderCustomComments(result);
      updateCustomCount();
    } catch (error) {
      if (version === state.customVersion)
        showError("#custom-preview-error", error);
    }
  });
}
function renderCustomComments(result) {
  $("#custom-comments").innerHTML =
    result.comments
      .map((comment) => {
        const checked =
          state.customSelectionMode === "include"
            ? state.customIds.has(comment.id)
            : !state.customIds.has(comment.id);
        return `<article class="comment-card"><label class="custom-comment-check"><input type="checkbox" data-custom-comment="${escapeHTML(comment.id)}" ${checked ? "checked" : ""} aria-label="提供 ${escapeHTML(comment.id)} 給模型"><span><span class="comment-meta"><strong>${escapeHTML(comment.id)}</strong><span>${escapeHTML(comment.date)}</span></span><span class="comment-body">${escapeHTML(comment.body)}</span><span class="comment-thread">${escapeHTML(comment.thread)}</span></span></label></article>`;
      })
      .join("") || '<p class="empty-state">這個範圍沒有留言。</p>';
  $("#custom-pagination").innerHTML =
    `<button type="button" class="button secondary" data-custom-page="${Math.max(0, result.offset - 40)}" ${result.offset === 0 ? "disabled" : ""}>← 上一頁</button><span>${result.count ? `${number(result.offset + 1)}–${number(Math.min(result.offset + 40, result.count))} / ` : ""}${number(result.count)} 則</span><button type="button" class="button secondary" data-custom-page="${result.offset + 40}" ${result.offset + 40 >= result.count ? "disabled" : ""}>下一頁 →</button>`;
  state.customPreview.page = result;
}
let customPageRequest = 0;
async function customPage(offset) {
  const session = state.customPreview?.session;
  if (!session) return;
  const request = ++customPageRequest;
  try {
    const result = await api("/api/custom-page", {
      ...context(),
      session,
      offset,
    });
    if (
      session === state.customPreview?.session &&
      request === customPageRequest
    )
      renderCustomComments(result);
  } catch (error) {
    if (session === state.customPreview?.session)
      showError("#custom-preview-error", error);
  }
}

function updateTemplateSummary() {
  const count = Object.keys(state.templateOverrides).length;
  $("#template-summary").textContent = count
    ? `已自訂 ${count} 份・下次產生時套用`
    : "使用預設分析規格";
}
async function loadTemplates() {
  if (state.templateCatalog) return;
  if (state.templatePromise) return state.templatePromise;
  state.templatePromise = (async () => {
    const data = await api("/api/templates");
    const overrides = {};
    try {
      const stored = localStorage.getItem(TEMPLATE_STORAGE_KEY);
      if (stored !== null) {
        const parsed = JSON.parse(stored);
        const saved = parsed?.templates;
        const names = new Set(data.templates.map((item) => item.name));
        if (
          parsed?.version !== 1 ||
          !saved ||
          typeof saved !== "object" ||
          Array.isArray(saved) ||
          Object.entries(saved).some(
            ([name, text]) =>
              !names.has(name) ||
              typeof text !== "string" ||
              !text.trim() ||
              text.length > data.max_characters,
          )
        ) {
          throw new Error("invalid stored templates");
        }
        Object.assign(overrides, saved);
      }
    } catch {
      state.templateWarning =
        "無法讀取已儲存的範本，目前使用預設內容。你可以重新編輯並儲存設定。";
      toast(state.templateWarning);
    }
    state.templateCatalog = data.templates;
    state.templateOverrides = overrides;
    $("#template-text").maxLength = data.max_characters;
    updateTemplateSummary();
  })();
  try {
    await state.templatePromise;
  } finally {
    state.templatePromise = null;
  }
}
function captureTemplateDraft() {
  if (!templateEditor.current) return;
  const item = state.templateCatalog.find(
    (entry) => entry.name === templateEditor.current,
  );
  const text = $("#template-text").value;
  if (text === item.default) delete templateEditor.drafts[item.name];
  else templateEditor.drafts[item.name] = text;
}
function renderTemplateEditor(name) {
  const item = state.templateCatalog.find((entry) => entry.name === name);
  if (!item) return;
  templateEditor.current = name;
  $("#template-type").replaceChildren(
    ...state.templateCatalog.map(
      (entry) =>
        new Option(
          entry.label +
            (Object.hasOwn(templateEditor.drafts, entry.name) ? " · 自訂" : ""),
          entry.name,
        ),
    ),
  );
  $("#template-type").value = name;
  $("#template-description").textContent = item.description;
  $("#template-text").value = templateEditor.drafts[name] ?? item.default;
  $("#template-count").textContent =
    `${number($("#template-text").value.length)} / ${number($("#template-text").maxLength)} 字元`;
  $("#template-variables").innerHTML = item.placeholders.length
    ? `<span>點選插入變數：</span>${item.placeholders.map((variable) => `<button type="button" data-variable="${escapeHTML(variable)}" aria-label="插入 ${escapeHTML(variable)}">${escapeHTML(variable)}</button>`).join("")}`
    : "這份共用範本使用純文字，不需插入變數。";
}
async function openTemplates() {
  const dialog = $("#template-dialog");
  $("#template-error").hidden = true;
  $("#template-form").hidden = true;
  $("#template-loading").hidden = false;
  if (!dialog.open) dialog.showModal();
  try {
    await loadTemplates();
    if (!dialog.open) return;
    templateEditor.drafts = { ...state.templateOverrides };
    const mode = $('input[name="mode"]:checked').value;
    const name =
      mode === "other"
        ? "custom_spec.md"
        : mode === "name"
          ? $("#two-step").checked
            ? $("#filter-strategy").value === "full"
              ? "full_filter_spec.md"
              : "filter_spec.md"
            : "report_spec.md"
          : mode === "features"
            ? $("#feature-analysis").value === "filter"
              ? "features_filter_spec.md"
              : "features_spec.md"
            : "roster_spec.md";
    renderTemplateEditor(name);
    $("#template-form").hidden = false;
    if (state.templateWarning)
      showError("#template-error", new Error(state.templateWarning));
  } catch (error) {
    showError("#template-error", error);
  } finally {
    $("#template-loading").hidden = true;
  }
}
function saveTemplates(event) {
  event.preventDefault();
  captureTemplateDraft();
  $("#template-error").hidden = true;
  for (const item of state.templateCatalog) {
    const text = templateEditor.drafts[item.name];
    if (text === undefined) continue;
    if (!text.trim() || text.length > $("#template-text").maxLength) {
      renderTemplateEditor(item.name);
      showError(
        "#template-error",
        new Error(`「${item.label}」不可留空，且最多 50,000 個字元。`),
      );
      $("#template-text").focus();
      return;
    }
  }
  const overrides = { ...templateEditor.drafts };
  try {
    localStorage.setItem(
      TEMPLATE_STORAGE_KEY,
      JSON.stringify({ version: 1, templates: overrides }),
    );
  } catch {
    showError(
      "#template-error",
      new Error(
        "無法儲存設定，請確認瀏覽器允許網站儲存資料，或清出儲存空間後再試。修改內容仍保留在編輯器中。",
      ),
    );
    return;
  }
  state.templateOverrides = overrides;
  syncCustomSeed();
  state.templateWarning = "";
  // Existing analysis snapshots stay valid; discard only pending generations
  // that used older settings, without interrupting browsing or verification.
  state.templateVersion++;
  updateTemplateSummary();
  $("#template-dialog").close();
  toast("已儲存範本設定，下次產生提示詞時套用。既有分析沿用原設定。");
}

$("#prompt-settings").addEventListener("click", openTemplates);
$("#edit-templates").addEventListener("click", openTemplates);
$("#close-templates").addEventListener("click", () =>
  $("#template-dialog").close(),
);
$("#cancel-templates").addEventListener("click", () =>
  $("#template-dialog").close(),
);
$("#template-type").addEventListener("change", (event) => {
  captureTemplateDraft();
  $("#template-error").hidden = true;
  renderTemplateEditor(event.target.value);
});
$("#template-text").addEventListener("input", () => {
  $("#template-count").textContent =
    `${number($("#template-text").value.length)} / ${number($("#template-text").maxLength)} 字元・尚未儲存`;
});
$("#reset-template").addEventListener("click", () => {
  delete templateEditor.drafts[templateEditor.current];
  renderTemplateEditor(templateEditor.current);
  $("#template-error").hidden = true;
  $("#template-count").textContent += "・儲存後恢復預設";
});
$("#template-variables").addEventListener("click", (event) => {
  const button = event.target.closest("[data-variable]");
  if (!button) return;
  const input = $("#template-text");
  const variable = button.dataset.variable;
  if (
    input.value.length -
      (input.selectionEnd - input.selectionStart) +
      variable.length >
    input.maxLength
  ) {
    showError(
      "#template-error",
      new Error("範本已達字數上限，請先刪除部分內容。"),
    );
    return;
  }
  input.setRangeText(variable, input.selectionStart, input.selectionEnd, "end");
  input.dispatchEvent(new Event("input"));
  input.focus();
});
$("#template-form").addEventListener("submit", saveTemplates);
loadTemplates().catch(() => {
  $("#template-summary").textContent = "範本尚未載入，請開啟設定重試";
});

$("#custom-instructions").addEventListener("input", invalidate);
$("#custom-preview-button").addEventListener("click", previewCustomComments);
$("#reload-custom-threads").addEventListener("click", () => {
  resetCustomSelection();
  invalidate();
  loadCustomThreads();
});
$("#custom-thread-search").addEventListener("input", renderCustomThreads);
$("#custom-all-threads").addEventListener("click", () => {
  state.customThreads = null;
  renderCustomThreads();
  resetCustomSelection();
  invalidate();
});
$("#custom-no-threads").addEventListener("click", () => {
  state.customThreads = new Set();
  renderCustomThreads();
  resetCustomSelection();
  invalidate();
});
$("#custom-threads").addEventListener("change", (event) => {
  const input = event.target.closest("[data-thread]");
  if (!input) return;
  const thread = state.customThreadOptions[Number(input.dataset.thread)];
  if (!thread) return;
  if (state.customThreads === null)
    state.customThreads = new Set(state.customThreadOptions.map((t) => t.id));
  const id = thread.id;
  if (input.checked) state.customThreads.add(id);
  else state.customThreads.delete(id);
  resetCustomSelection();
  invalidate();
  renderCustomThreads();
});
["custom-ranges", "custom-limit", "custom-order"].forEach((id) =>
  $(`#${id}`).addEventListener("input", () => {
    resetCustomSelection();
    invalidate();
  }),
);
$("#custom-comments").addEventListener("change", (event) => {
  const input = event.target.closest("[data-custom-comment]");
  if (!input) return;
  const id = input.dataset.customComment;
  if (input.checked === (state.customSelectionMode === "include"))
    state.customIds.add(id);
  else state.customIds.delete(id);
  invalidate();
  updateCustomCount();
});
$("#custom-pagination").addEventListener("click", (event) => {
  const button = event.target.closest("[data-custom-page]");
  if (button) customPage(Number(button.dataset.customPage));
});
for (const [id, mode] of [
  ["custom-select-all", "exclude"],
  ["custom-select-none", "include"],
]) {
  $(`#${id}`).addEventListener("click", () => {
    if (!state.customPreview) return;
    state.customSelectionMode = mode;
    state.customIds = new Set();
    invalidate();
    renderCustomComments(state.customPreview.page);
    updateCustomCount();
  });
}
let customDateTimer;
function customDatesChanged() {
  resetCustomSelection();
  clearTimeout(customDateTimer);
  if ($('input[name="mode"]:checked').value === "other")
    customDateTimer = setTimeout(loadCustomThreads, 250);
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
    resetCustomSelection();
    renderMode();
    invalidate();
  }),
);
[
  "target",
  "start",
  "end",
  "depth",
  "two-step",
  "filter-strategy",
  "feature-analysis",
  "batch-comments",
].forEach((id) =>
  $(`#${id}`).addEventListener("input", () => {
    if (["two-step", "filter-strategy", "feature-analysis"].includes(id))
      updateFilterOptions();
    if (id === "start" || id === "end") customDatesChanged();
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
  if (button.dataset.fullNav !== undefined)
    fullNavigate(Number(button.dataset.fullNav));
  if (button.id === "full-finalize") finalizeFullFilter();
  if (
    button.id === "full-discard" &&
    state.generated?.stage === "full_filter"
  ) {
    state.fullDrafts.delete(fullDraftKey());
    $("#filter-answer").value = state.generated.filter.saved_answer;
    updateFullControls();
  }
  if (button.id === "go-verify") {
    switchTab("verify");
    $("#report").focus();
  }
});
$("#prompt-result").addEventListener("input", (event) => {
  if (
    event.target.id === "filter-answer" &&
    state.generated?.stage === "full_filter"
  ) {
    captureFullDraft();
    updateFullControls();
  }
});
$("#prompt-result").addEventListener("change", (event) => {
  if (event.target.id === "full-batch-select")
    fullNavigate(Number(event.target.value));
});
$("#full-resume").addEventListener("click", resumeFullFilter);
updateFullResume();
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
