"use strict";

/* API 上游配置与 AI 对话窗口。依赖 backend.js 中的 backendConnected / backendFetch / refreshBackend。 */

const aiProviderErrorLabels = {
  AI_PROVIDER_NOT_CONFIGURED: "尚未配置 API 上游，请先填写地址、模型和密钥",
  AI_PROVIDER_INCOMPLETE: "接口地址和文字模型都必须填写",
  AI_PROVIDER_INVALID_ENDPOINT: "接口地址填写到根路径即可，例如 https://host/v1 或 https://openrouter.ai/api/v1，不要带 /chat/completions 或查询参数",
  AI_PROVIDER_INVALID_PROTOCOL: "上游协议只支持 Chat Completions 或 Responses API",
  AI_PROVIDER_INVALID_KEY: "API 密钥格式不正确（至少 8 个可见字符）",
  AI_PROVIDER_INVALID_CONFIG: "配置无法创建客户端，请检查地址与协议",
  AI_PROVIDER_UNREACHABLE: "无法连接该上游或读取模型列表，请检查地址、密钥和网络",
  AI_PROVIDER_TEST_FAILED: "单题测试失败，请检查上游状态与模型名",
  AI_PROVIDER_INVALID_PROXY: "代理地址要写成 http://主机:端口，例如 http://127.0.0.1:7890，留空表示直连",
  AI_PROVIDER_INVALID_FIELD: "配置项格式不正确",
};

const aiProviderSelectors = {
  panel: "#ai-provider-panel",
  form: "#ai-provider-form",
  status: "#ai-provider-status",
  note: "#ai-provider-note",
  error: "#ai-provider-error",
  result: "#ai-provider-result",
  endpoint: "#ai-provider-endpoint",
  key: "#ai-provider-key",
  model: "#ai-provider-model",
  visionModel: "#ai-provider-vision",
  fallbacks: "#ai-provider-fallbacks",
  timeout: "#ai-provider-timeout",
  proxy: "#ai-provider-proxy",
  models: "#ai-provider-models",
  save: "#ai-provider-save",
  fetchModels: "#ai-provider-fetch",
  test: "#ai-provider-test",
  clearKey: "#ai-provider-clear-key",
  protocolChat: "#ai-protocol-chat",
  protocolResponses: "#ai-protocol-responses",
  chatList: "#ai-chat-list",
  chatEmpty: "#ai-chat-empty",
  chatError: "#ai-chat-error",
  chatState: "#ai-chat-state",
  chatSummary: "#ai-chat-summary",
  chatRefresh: "#ai-chat-refresh",
};

const aiProviderEl = {};
let aiProviderState = { configured: false, protocol: "chat", has_key: false, source: "" };
let aiProviderBusy = false;
let aiChatVisible = false;
let aiChatTimer = null;
let aiChatBusy = false;
let aiChatLatest = 0;
let aiChatRows = [];

function aiProviderErrorText(code) {
  return aiProviderErrorLabels[code] || code || "请求失败";
}

function aiProviderProtocolLabel(protocol) {
  return protocol === "responses" ? "Responses API" : "Chat Completions";
}

/* 把表单输入整理成后端要求的请求体；纯函数，便于测试。 */
function aiProviderPayload(input = {}) {
  const payload = {
    endpoint: String(input.endpoint ?? "").trim(),
    protocol: input.protocol === "responses" ? "responses" : "chat",
    model: String(input.model ?? "").trim(),
    vision_model: String(input.visionModel ?? "").trim(),
    timeout: Number(input.timeout) || 180,
    proxy: String(input.proxy ?? "").trim(),
  };
  const raw = String(input.fallbacks ?? "").trim();
  if (raw) {
    let parsed;
    try {
      parsed = JSON.parse(raw);
    } catch {
      throw new Error("备用模型必须写成 JSON 数组，例如 [\"backup-model\"]");
    }
    if (!Array.isArray(parsed) || parsed.some((item) => typeof item !== "string")) {
      throw new Error("备用模型必须写成 JSON 数组，例如 [\"backup-model\"]");
    }
    payload.model_fallbacks = parsed;
  } else {
    payload.model_fallbacks = [];
  }
  const key = String(input.key ?? "").trim();
  if (key) payload.api_key = key;
  if (input.clearKey === true) payload.clear_key = true;
  return payload;
}

async function aiProviderFetch(path, options = {}, timeout = 200000) {
  const response = await fetch("/api/backend" + path, {
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
    signal: AbortSignal.timeout(timeout),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(aiProviderErrorText(body.error) || `请求失败 (${response.status})`);
  return body;
}

function aiProviderConnected() {
  return typeof backendConnected !== "undefined" && backendConnected;
}

function aiProviderFocused() {
  const active = document.activeElement;
  return Boolean(active && aiProviderEl.form && aiProviderEl.form.contains?.(active));
}

function showProviderError(message) {
  if (!aiProviderEl.error) return;
  aiProviderEl.error.textContent = message || "操作失败";
  aiProviderEl.error.hidden = false;
}

function showProviderResult(message) {
  if (!aiProviderEl.result) return;
  aiProviderEl.result.textContent = message;
  aiProviderEl.result.hidden = !message;
}

function setProviderEnabled(enabled, statusText) {
  const controls = [
    aiProviderEl.endpoint, aiProviderEl.key, aiProviderEl.model, aiProviderEl.visionModel,
    aiProviderEl.fallbacks, aiProviderEl.timeout, aiProviderEl.proxy, aiProviderEl.save, aiProviderEl.fetchModels,
    aiProviderEl.test, aiProviderEl.clearKey, aiProviderEl.protocolChat, aiProviderEl.protocolResponses,
  ];
  for (const control of controls) {
    if (control) control.disabled = !enabled || aiProviderBusy;
  }
  if (!enabled && statusText && aiProviderEl.status) aiProviderEl.status.textContent = statusText;
  if (aiProviderEl.panel) aiProviderEl.panel.dataset.ready = String(enabled);
}

function renderProtocol() {
  const protocol = aiProviderState.protocol === "responses" ? "responses" : "chat";
  if (aiProviderEl.protocolChat) aiProviderEl.protocolChat.setAttribute("aria-pressed", String(protocol === "chat"));
  if (aiProviderEl.protocolResponses) {
    aiProviderEl.protocolResponses.setAttribute("aria-pressed", String(protocol === "responses"));
  }
}

function applyAIProvider(status = {}, force = false) {
  aiProviderState = {
    configured: Boolean(status.configured),
    protocol: status.protocol === "responses" ? "responses" : "chat",
    has_key: Boolean(status.has_key),
    source: String(status.source || ""),
  };
  setProviderEnabled(true);
  const editable = force || !aiProviderFocused();
  if (editable) {
    if (aiProviderEl.endpoint) aiProviderEl.endpoint.value = status.endpoint || "";
    if (aiProviderEl.model) aiProviderEl.model.value = status.model || "";
    if (aiProviderEl.visionModel) aiProviderEl.visionModel.value = status.vision_model || "";
    if (aiProviderEl.fallbacks) {
      const fallbacks = Array.isArray(status.model_fallbacks) ? status.model_fallbacks : [];
      aiProviderEl.fallbacks.value = fallbacks.length ? JSON.stringify(fallbacks) : "";
    }
    if (aiProviderEl.timeout) aiProviderEl.timeout.value = String(status.timeout || 180);
    if (aiProviderEl.proxy) aiProviderEl.proxy.value = status.proxy || "";
    if (aiProviderEl.key) aiProviderEl.key.value = "";
  }
  if (aiProviderEl.key) {
    aiProviderEl.key.placeholder = aiProviderState.has_key ? "已保存，留空则不修改" : "尚未保存密钥";
  }
  renderProtocol();
  const source = aiProviderState.source === "console"
    ? "控制台配置"
    : aiProviderState.source === "environment" ? "服务器环境变量" : "未配置";
  if (aiProviderEl.status) {
    aiProviderEl.status.textContent = aiProviderState.configured
      ? `已就绪 · ${aiProviderProtocolLabel(aiProviderState.protocol)} · ${status.model || "已配置模型"} · ${source}`
      : `未配置 · ${source}`;
  }
  if (aiProviderEl.note) {
    aiProviderEl.note.textContent = aiProviderState.has_key
      ? "密钥保存在后端数据库的加密字段中，页面不回显；留空保存即保持原密钥。"
      : "尚未保存密钥，API 通道暂不可用；密钥只写入后端数据库的加密字段。";
  }
}

async function refreshAIProvider(force = false) {
  if (!aiProviderEl.panel) return;
  if (!aiProviderConnected()) {
    setProviderEnabled(false, "等待后端连接");
    return;
  }
  try {
    const status = await aiProviderFetch("/ai/provider", {}, 20000);
    applyAIProvider(status, force);
    if (aiProviderEl.error) aiProviderEl.error.hidden = true;
  } catch (error) {
    setProviderEnabled(false, "无法读取上游配置");
    showProviderError(error.message);
  }
}

async function saveAIProvider(event) {
  if (event) event.preventDefault();
  if (!aiProviderEl.form || aiProviderBusy) return;
  aiProviderBusy = true;
  setProviderEnabled(true);
  if (aiProviderEl.error) aiProviderEl.error.hidden = true;
  showProviderResult("");
  try {
    const payload = aiProviderPayload({
      endpoint: aiProviderEl.endpoint?.value,
      model: aiProviderEl.model?.value,
      visionModel: aiProviderEl.visionModel?.value,
      fallbacks: aiProviderEl.fallbacks?.value,
      timeout: aiProviderEl.timeout?.value,
      proxy: aiProviderEl.proxy?.value,
      key: aiProviderEl.key?.value,
      protocol: aiProviderState.protocol,
    });
    const status = await aiProviderFetch("/ai/provider", {
      method: "PUT", body: JSON.stringify(payload),
    }, 30000);
    applyAIProvider(status, true);
    showProviderResult("上游配置已保存，之后的答题请求会按新配置发送。");
    if (typeof refreshBackend === "function") void refreshBackend();
  } catch (error) {
    showProviderError(error.message);
  } finally {
    aiProviderBusy = false;
    setProviderEnabled(aiProviderConnected());
    renderProtocol();
  }
}

async function fetchAIProviderModels() {
  if (aiProviderBusy) return;
  aiProviderBusy = true;
  setProviderEnabled(true);
  showProviderResult("正在读取上游模型列表…");
  try {
    const body = await aiProviderFetch("/ai/models", {}, 60000);
    const models = Array.isArray(body.models) ? body.models : [];
    if (aiProviderEl.models) {
      const options = models.slice(0, 200).map((item) => {
        const option = document.createElement("option");
        option.value = String(item.id || "");
        option.label = String(item.id || "");
        return option;
      }).filter((option) => option.value);
      aiProviderEl.models.replaceChildren(...options);
    }
    showProviderResult(models.length
      ? `已获取 ${models.length} 个模型，可在模型输入框中直接选择或继续手动填写。`
      : "上游没有返回模型列表，可手动填写模型名。");
    if (aiProviderEl.error) aiProviderEl.error.hidden = true;
  } catch (error) {
    showProviderResult("");
    showProviderError(error.message);
  } finally {
    aiProviderBusy = false;
    setProviderEnabled(aiProviderConnected());
  }
}

async function testAIProvider() {
  if (aiProviderBusy) return;
  aiProviderBusy = true;
  setProviderEnabled(true);
  showProviderResult("正在用当前配置发送一道测试题…");
  try {
    const body = await aiProviderFetch("/ai/test", { method: "POST", body: "{}" }, 240000);
    const reply = String(body.reply || "").replace(/\s+/g, " ").slice(0, 160);
    showProviderResult(`测试成功 · ${body.model || "模型"} · ${(Number(body.elapsed_ms || 0) / 1000).toFixed(1)} 秒 · 回复：${reply || "（空）"}`);
    if (aiProviderEl.error) aiProviderEl.error.hidden = true;
  } catch (error) {
    showProviderResult("");
    showProviderError(error.message);
  } finally {
    aiProviderBusy = false;
    setProviderEnabled(aiProviderConnected());
  }
}

async function clearAIProviderKey() {
  if (aiProviderBusy) return;
  aiProviderBusy = true;
  setProviderEnabled(true);
  try {
    const status = await aiProviderFetch("/ai/provider", {
      method: "PUT", body: JSON.stringify({ clear_key: true }),
    }, 30000);
    applyAIProvider(status, true);
    showProviderResult("已清除保存的 API 密钥，API 通道暂停使用。");
    if (typeof refreshBackend === "function") void refreshBackend();
  } catch (error) {
    showProviderError(error.message);
  } finally {
    aiProviderBusy = false;
    setProviderEnabled(aiProviderConnected());
    renderProtocol();
  }
}

function setProtocol(protocol) {
  if (aiProviderBusy) return;
  aiProviderState.protocol = protocol === "responses" ? "responses" : "chat";
  renderProtocol();
  showProviderResult(`已选择 ${aiProviderProtocolLabel(aiProviderState.protocol)}，保存后生效。`);
}

/* ---- 图片 API 上游 ---- */

const aiVisionProviderSelectors = {
  panel: "#ai-vision-provider-panel",
  form: "#ai-vision-provider-form",
  status: "#ai-vision-provider-status",
  note: "#ai-vision-provider-note",
  error: "#ai-vision-provider-error",
  bench: "#ai-vision-bench-result",
  endpoint: "#ai-vision-provider-endpoint",
  key: "#ai-vision-provider-key",
  model: "#ai-vision-provider-model",
  fallbacks: "#ai-vision-provider-fallbacks",
  models: "#ai-vision-provider-models",
  save: "#ai-vision-provider-save",
  fetchModels: "#ai-vision-provider-fetch",
  benchRun: "#ai-vision-provider-bench",
  clearKey: "#ai-vision-provider-clear-key",
  protocolInherit: "#ai-vision-protocol-inherit",
  protocolChat: "#ai-vision-protocol-chat",
  protocolResponses: "#ai-vision-protocol-responses",
};

const aiVisionEl = {};
let aiVisionState = { configured: false, inherited: true, protocol: "chat", own_protocol: false, has_key: false };
let aiVisionBusy = false;

/* 图片通道留空的字段由后端沿用文本通道，所以请求体只带真正填写的项。 */
function aiVisionProviderPayload(input = {}) {
  const model = String(input.model ?? "").trim();
  if (!model) throw new Error("图片模型必须填写，例如 nvidia/nemotron-3.5-super-vl:free");
  const payload = { model };
  const endpoint = String(input.endpoint ?? "").trim();
  if (endpoint) payload.endpoint = endpoint;
  const protocol = String(input.protocol ?? "").trim();
  if (protocol === "chat" || protocol === "responses") payload.protocol = protocol;
  const raw = String(input.fallbacks ?? "").trim();
  if (raw) {
    let parsed;
    try {
      parsed = JSON.parse(raw);
    } catch {
      throw new Error("备用图片模型必须写成 JSON 数组，例如 [\"backup-vision-model\"]");
    }
    if (!Array.isArray(parsed) || parsed.some((item) => typeof item !== "string")) {
      throw new Error("备用图片模型必须写成 JSON 数组，例如 [\"backup-vision-model\"]");
    }
    payload.model_fallbacks = parsed;
  } else {
    payload.model_fallbacks = [];
  }
  const key = String(input.key ?? "").trim();
  if (key) payload.api_key = key;
  if (input.clearKey === true) payload.clear_key = true;
  return payload;
}

function setVisionProviderEnabled(enabled, statusText) {
  const controls = [
    aiVisionEl.endpoint, aiVisionEl.key, aiVisionEl.model, aiVisionEl.fallbacks, aiVisionEl.save,
    aiVisionEl.fetchModels, aiVisionEl.benchRun, aiVisionEl.clearKey, aiVisionEl.protocolInherit,
    aiVisionEl.protocolChat, aiVisionEl.protocolResponses,
  ];
  for (const control of controls) {
    if (control) control.disabled = !enabled || aiVisionBusy;
  }
  if (!enabled && statusText && aiVisionEl.status) aiVisionEl.status.textContent = statusText;
  if (aiVisionEl.panel) aiVisionEl.panel.dataset.ready = String(enabled);
}

function renderVisionProtocol() {
  const protocol = aiVisionState.own_protocol ? aiVisionState.protocol : "inherit";
  for (const [name, element] of [
    ["inherit", aiVisionEl.protocolInherit],
    ["chat", aiVisionEl.protocolChat],
    ["responses", aiVisionEl.protocolResponses],
  ]) {
    if (element) element.setAttribute("aria-pressed", String(protocol === name));
  }
}

function renderVisionBench(result = null) {
  if (!aiVisionEl.bench) return;
  if (!result) {
    aiVisionEl.bench.replaceChildren();
    aiVisionEl.bench.hidden = true;
    return;
  }
  const rows = [];
  const summary = document.createElement("li");
  summary.className = "ai-vision-bench-summary";
  const total = Number(result.available || 0);
  const graded = Number(result.graded || 0);
  summary.dataset.state = result.accuracy === 1 ? "ok" : (graded ? "warn" : "unknown");
  summary.textContent = total
    ? `图片实测：${graded ? `答对 ${result.correct} / ${graded} 题（正确率 ${Math.round(result.accuracy * 100)}%）` : "暂无可对照的题目"} · 模型 ${result.model || "未知"}${result.mode === "direct" ? " · 单次识图直答" : " · 三步流水线"}`
    : "题库里还没有带答案的图片题，等积累几道后再测。";
  rows.push(summary);
  for (const item of Array.isArray(result.items) ? result.items : []) {
    const row = document.createElement("li");
    row.className = "ai-vision-bench-row";
    row.dataset.state = item.ok === true ? "ok" : item.ok === false ? "error" : "unknown";
    const mark = item.ok === true ? "对" : item.ok === false ? "错" : "?";
    row.textContent = [
      mark,
      `第${String(item.id || "").slice(0, 6)}题`,
      `${item.type || "?"} · ${item.images} 图`,
      `标准 ${item.expected || "-"}`,
      `实测 ${item.got || "-"}`,
      `${Number(item.seconds || 0).toFixed(1)} 秒`,
      item.error || "",
    ].filter(Boolean).join(" · ");
    rows.push(row);
  }
  aiVisionEl.bench.replaceChildren(...rows);
  aiVisionEl.bench.hidden = false;
}

function applyVisionProvider(status = {}, force = false) {
  aiVisionState = {
    configured: Boolean(status.configured),
    inherited: Boolean(status.inherited),
    protocol: status.protocol === "responses" ? "responses" : "chat",
    own_protocol: Boolean(status.source === "console" && status.protocol),
    has_key: Boolean(status.has_key),
    source: String(status.source || ""),
  };
  setVisionProviderEnabled(true);
  const active = document.activeElement;
  const editable = force || !(active && aiVisionEl.form && aiVisionEl.form.contains?.(active));
  if (editable) {
    if (aiVisionEl.endpoint) aiVisionEl.endpoint.value = status.inherited ? "" : (status.endpoint || "");
    if (aiVisionEl.model) aiVisionEl.model.value = status.inherited ? "" : (status.model || "");
    if (aiVisionEl.fallbacks) {
      const fallbacks = Array.isArray(status.model_fallbacks) ? status.model_fallbacks : [];
      aiVisionEl.fallbacks.value = status.inherited || !fallbacks.length ? "" : JSON.stringify(fallbacks);
    }
    if (aiVisionEl.key) aiVisionEl.key.value = "";
  }
  if (aiVisionEl.key) {
    aiVisionEl.key.placeholder = aiVisionState.source === "console" && aiVisionState.has_key
      ? "已保存图片通道密钥，留空则不修改"
      : "留空沿用文本 API 的密钥";
  }
  renderVisionProtocol();
  const statusText = aiVisionState.source === "console"
    ? `已单独配置 · ${aiProviderProtocolLabel(aiVisionState.protocol)} · ${status.model || "已配置模型"}`
    : "未单独配置 · 图片题沿用文本 API";
  if (aiVisionEl.status) aiVisionEl.status.textContent = statusText;
  if (aiVisionEl.note) {
    aiVisionEl.note.textContent = aiVisionState.source === "console"
      ? "图片题会先用这里的模型识图答题，失败再退回网页 AI（可在答题通道里关掉）。"
      : "留空的项沿用文本 API 配置；只想换图片模型时填一个模型名并保存即可。";
  }
}

async function refreshVisionProvider(force = false) {
  if (!aiVisionEl.panel) return;
  if (!aiProviderConnected()) {
    setVisionProviderEnabled(false, "等待后端连接");
    return;
  }
  try {
    const status = await aiProviderFetch("/ai/vision-provider", {}, 20000);
    applyVisionProvider(status, force);
    if (aiVisionEl.error) aiVisionEl.error.hidden = true;
  } catch (error) {
    setVisionProviderEnabled(false, "无法读取图片上游配置");
    if (aiVisionEl.error) {
      aiVisionEl.error.textContent = error.message;
      aiVisionEl.error.hidden = false;
    }
  }
}

async function saveVisionProvider(event) {
  if (event) event.preventDefault();
  if (!aiVisionEl.form || aiVisionBusy) return;
  aiVisionBusy = true;
  setVisionProviderEnabled(true);
  if (aiVisionEl.error) aiVisionEl.error.hidden = true;
  try {
    const payload = aiVisionProviderPayload({
      endpoint: aiVisionEl.endpoint?.value,
      model: aiVisionEl.model?.value,
      fallbacks: aiVisionEl.fallbacks?.value,
      key: aiVisionEl.key?.value,
      protocol: aiVisionState.own_protocol ? aiVisionState.protocol : "",
    });
    const status = await aiProviderFetch("/ai/vision-provider", {
      method: "PUT", body: JSON.stringify(payload),
    }, 30000);
    applyVisionProvider(status, true);
    renderVisionBench(null);
    if (aiVisionEl.note) aiVisionEl.note.textContent = "图片 API 已保存，之后的图片题会先用它答题。";
    if (typeof refreshBackend === "function") void refreshBackend();
  } catch (error) {
    if (aiVisionEl.error) {
      aiVisionEl.error.textContent = error.message;
      aiVisionEl.error.hidden = false;
    }
  } finally {
    aiVisionBusy = false;
    setVisionProviderEnabled(aiProviderConnected());
    renderVisionProtocol();
  }
}

async function fetchVisionModels() {
  if (aiVisionBusy) return;
  aiVisionBusy = true;
  setVisionProviderEnabled(true);
  try {
    const body = await aiProviderFetch("/ai/models?route=vision", {}, 60000);
    const models = (Array.isArray(body.models) ? body.models : []).filter((item) => item?.image);
    if (aiVisionEl.models) {
      aiVisionEl.models.replaceChildren(...models.slice(0, 300).map((item) => {
        const option = document.createElement("option");
        option.value = String(item.id || "");
        option.label = String(item.id || "");
        return option;
      }).filter((option) => option.value));
    }
    if (aiVisionEl.note) {
      aiVisionEl.note.textContent = models.length
        ? `上游有 ${models.length} 个支持图片输入的模型，可在图片模型输入框中直接选。`
        : "上游没有标注支持图片输入的模型，可手动填写模型名。";
    }
  } catch (error) {
    if (aiVisionEl.error) {
      aiVisionEl.error.textContent = error.message;
      aiVisionEl.error.hidden = false;
    }
  } finally {
    aiVisionBusy = false;
    setVisionProviderEnabled(aiProviderConnected());
  }
}

async function runVisionBench() {
  if (aiVisionBusy) return;
  aiVisionBusy = true;
  setVisionProviderEnabled(true);
  renderVisionBench(null);
  if (aiVisionEl.note) aiVisionEl.note.textContent = "正在用图片 API 重答题库里已有的图片题，稍等…";
  try {
    const result = await aiProviderFetch("/ai/vision-bench", {
      method: "POST", body: JSON.stringify({ limit: 6, mode: "direct" }),
    }, 300000);
    renderVisionBench(result);
    if (aiVisionEl.error) aiVisionEl.error.hidden = true;
  } catch (error) {
    if (aiVisionEl.error) {
      aiVisionEl.error.textContent = error.message;
      aiVisionEl.error.hidden = false;
    }
  } finally {
    aiVisionBusy = false;
    setVisionProviderEnabled(aiProviderConnected());
  }
}

async function clearVisionProviderKey() {
  if (aiVisionBusy) return;
  aiVisionBusy = true;
  setVisionProviderEnabled(true);
  try {
    const status = await aiProviderFetch("/ai/vision-provider", {
      method: "PUT", body: JSON.stringify({ clear_key: true }),
    }, 30000);
    applyVisionProvider(status, true);
    if (aiVisionEl.note) aiVisionEl.note.textContent = "已清除图片通道自己的密钥，之后沿用文本 API 的密钥。";
  } catch (error) {
    if (aiVisionEl.error) {
      aiVisionEl.error.textContent = error.message;
      aiVisionEl.error.hidden = false;
    }
  } finally {
    aiVisionBusy = false;
    setVisionProviderEnabled(aiProviderConnected());
  }
}

function setVisionProtocol(protocol) {
  if (aiVisionBusy) return;
  aiVisionState.own_protocol = protocol !== "";
  if (protocol) aiVisionState.protocol = protocol === "responses" ? "responses" : "chat";
  renderVisionProtocol();
  if (aiVisionEl.note) {
    aiVisionEl.note.textContent = protocol
      ? `图片通道协议已选择 ${aiProviderProtocolLabel(protocol)}，保存后生效。`
      : "图片通道协议跟随文本 API，保存后生效。";
  }
}

function visionProviderViewChanged(view) {
  if (view === "connection") void refreshVisionProvider(false);
}

/* ---- AI 对话窗口 ---- */

function aiChatTime(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value) || value <= 0) return "--";
  return new Date(value * 1000).toLocaleTimeString();
}

function aiChatBlock(label, text) {
  const details = document.createElement("details");
  const summary = document.createElement("summary");
  summary.textContent = label;
  const pre = document.createElement("pre");
  pre.textContent = text || "（空）";
  details.append(summary, pre);
  return details;
}

function renderAIChatItem(row = {}) {
  const item = document.createElement("li");
  item.className = "ai-chat-item";
  const ok = row.status === "ok";
  item.dataset.status = ok ? "ok" : "error";
  const header = document.createElement("header");
  const channel = document.createElement("span");
  channel.className = "ai-chat-channel";
  channel.textContent = `${row.channel === "api" ? "API AI" : "网页 AI"} · ${aiProviderProtocolLabel(row.protocol)}`;
  const model = document.createElement("span");
  model.className = "ai-chat-model";
  model.textContent = row.model || "未知模型";
  const time = document.createElement("time");
  time.textContent = aiChatTime(row.created_at);
  const badge = document.createElement("span");
  badge.className = "ai-chat-badge";
  badge.dataset.state = ok ? "ok" : "error";
  badge.textContent = ok ? "成功" : "失败";
  header.append(channel, model, time, badge);
  const meta = document.createElement("p");
  meta.className = "ai-chat-meta";
  meta.textContent = [
    row.route === "vision" ? "识图" : "文字",
    row.stage ? `阶段 ${row.stage}` : "",
    `${(Number(row.elapsed_ms || 0) / 1000).toFixed(1)} 秒`,
    `${Number(row.prompt_chars || 0)} / ${Number(row.response_chars || 0)} 字`,
    Number(row.images || 0) ? `${row.images} 张图片` : "",
    row.job_id ? `任务 ${String(row.job_id).slice(0, 8)}` : "手动测试",
    row.truncated ? "内容已截断" : "",
  ].filter(Boolean).join(" · ");
  item.append(header, meta);
  if (!ok && row.error) {
    const error = document.createElement("p");
    error.className = "ai-chat-failure";
    error.textContent = row.error;
    item.append(error);
  }
  item.append(aiChatBlock("请求", row.prompt), aiChatBlock("回复", row.response));
  return item;
}

function setChatState(state) {
  if (!aiProviderEl.chatState) return;
  const labels = { live: "实时更新", error: "更新失败", waiting: "待连接" };
  aiProviderEl.chatState.dataset.state = state === "live" ? "online" : state === "error" ? "degraded" : "unknown";
  aiProviderEl.chatState.textContent = labels[state] || "待连接";
}

function scheduleAIChat(delay) {
  clearTimeout(aiChatTimer);
  aiChatTimer = null;
  if (!aiChatVisible) return;
  aiChatTimer = setTimeout(() => { void loadAIChat(false); }, delay);
}

async function loadAIChat(reset = false) {
  if (!aiProviderEl.chatList || !aiChatVisible || aiChatBusy) return;
  if (!aiProviderConnected()) {
    setChatState("waiting");
    scheduleAIChat(5000);
    return;
  }
  aiChatBusy = true;
  try {
    const query = reset || !aiChatLatest ? "?limit=40" : `?after=${aiChatLatest}&limit=40`;
    const body = await aiProviderFetch("/ai/conversations" + query, {}, 20000);
    const rows = Array.isArray(body.conversations) ? body.conversations : [];
    if (reset) {
      aiChatLatest = 0;
      aiChatRows = [];
      aiProviderEl.chatList.replaceChildren();
    }
    let added = 0;
    // 后端按最新在前返回，逐条 prepend 需要反向遍历才能保持最新在最前。
    for (const row of [...rows].reverse()) {
      if (!row || !Number.isSafeInteger(row.id)) continue;
      if (aiChatRows.some((item) => item.id === row.id)) continue;
      aiChatRows.push(row);
      aiProviderEl.chatList.prepend(renderAIChatItem(row));
      aiChatLatest = Math.max(aiChatLatest, row.id);
      added += 1;
    }
    while (aiProviderEl.chatList.children.length > 60) {
      aiProviderEl.chatList.children[aiProviderEl.chatList.children.length - 1].remove();
    }
    if (aiChatRows.length > 60) aiChatRows = aiChatRows.slice(-60);
    if (aiProviderEl.chatEmpty) aiProviderEl.chatEmpty.hidden = aiChatRows.length > 0;
    if (aiProviderEl.chatError) aiProviderEl.chatError.hidden = true;
    setChatState("live");
    if (aiProviderEl.chatSummary && (added || reset)) {
      aiProviderEl.chatSummary.textContent = aiChatRows.length
        ? `显示最近 ${aiChatRows.length} 条 AI 请求，最新的在最前`
        : "显示后端最近发出的 AI 请求与模型回复";
    }
  } catch (error) {
    setChatState("error");
    if (aiProviderEl.chatError) {
      aiProviderEl.chatError.textContent = `AI 对话读取失败：${error.message}`;
      aiProviderEl.chatError.hidden = false;
    }
  } finally {
    aiChatBusy = false;
    scheduleAIChat(4000);
  }
}

function aiChatViewChanged(view) {
  const visible = view === "ai-chat";
  if (visible === aiChatVisible) return;
  aiChatVisible = visible;
  clearTimeout(aiChatTimer);
  aiChatTimer = null;
  if (visible) void loadAIChat(true);
}

function aiProviderViewChanged(view) {
  if (view === "connection") void refreshAIProvider(false);
  if (view === "connection") void refreshVisionProvider(false);
}

function aiProviderInit() {
  for (const [key, selector] of Object.entries(aiProviderSelectors)) {
    try {
      aiProviderEl[key] = document.querySelector(selector);
    } catch {
      aiProviderEl[key] = null;
    }
  }
  for (const [key, selector] of Object.entries(aiVisionProviderSelectors)) {
    try {
      aiVisionEl[key] = document.querySelector(selector);
    } catch {
      aiVisionEl[key] = null;
    }
  }
  if (!aiProviderEl.panel || !aiProviderEl.form) return;
  aiProviderEl.form.addEventListener("submit", (event) => void saveAIProvider(event));
  aiProviderEl.protocolChat?.addEventListener("click", () => setProtocol("chat"));
  aiProviderEl.protocolResponses?.addEventListener("click", () => setProtocol("responses"));
  aiProviderEl.fetchModels?.addEventListener("click", () => void fetchAIProviderModels());
  aiProviderEl.test?.addEventListener("click", () => void testAIProvider());
  aiProviderEl.clearKey?.addEventListener("click", () => void clearAIProviderKey());
  aiProviderEl.chatRefresh?.addEventListener("click", () => void loadAIChat(true));
  if (aiProviderEl.protocolChat) aiProviderEl.protocolChat.setAttribute("aria-pressed", "true");
  renderProtocol();

  if (aiVisionEl.panel && aiVisionEl.form) {
    aiVisionEl.form.addEventListener("submit", (event) => void saveVisionProvider(event));
    aiVisionEl.protocolInherit?.addEventListener("click", () => setVisionProtocol(""));
    aiVisionEl.protocolChat?.addEventListener("click", () => setVisionProtocol("chat"));
    aiVisionEl.protocolResponses?.addEventListener("click", () => setVisionProtocol("responses"));
    aiVisionEl.fetchModels?.addEventListener("click", () => void fetchVisionModels());
    aiVisionEl.benchRun?.addEventListener("click", () => void runVisionBench());
    aiVisionEl.clearKey?.addEventListener("click", () => void clearVisionProviderKey());
    renderVisionProtocol();
  }
}

aiProviderInit();
