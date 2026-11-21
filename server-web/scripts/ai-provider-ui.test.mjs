import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = fs.readFileSync(
  new URL("../internal/server/static/ai-provider.js", import.meta.url), "utf8",
);

class Element {
  constructor(tag = "div") {
    this.tag = tag;
    this.children = [];
    this.listeners = new Map();
    this.attributes = new Map();
    this.dataset = {};
    this.value = "";
    this.label = "";
    this.placeholder = "";
    this.textContent = "";
    this.className = "";
    this.hidden = false;
    this.disabled = false;
    this.parentElement = null;
    this.isConnected = true;
  }
  addEventListener(name, handler) {
    const handlers = this.listeners.get(name) || [];
    handlers.push(handler);
    this.listeners.set(name, handlers);
  }
  fire(name) {
    const event = { target: this, currentTarget: this, preventDefault() {} };
    for (const handler of this.listeners.get(name) || []) handler(event);
    return event;
  }
  setAttribute(name, value) { this.attributes.set(name, value); }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  append(...items) {
    for (const item of items) {
      item.parentElement = this;
      this.children.push(item);
    }
  }
  prepend(...items) {
    for (const item of [...items].reverse()) {
      item.parentElement = this;
      this.children.unshift(item);
    }
  }
  replaceChildren(...items) {
    this.children = [];
    this.append(...items);
  }
  remove() {
    if (!this.parentElement) return;
    const siblings = this.parentElement.children;
    const index = siblings.indexOf(this);
    if (index >= 0) siblings.splice(index, 1);
    this.parentElement = null;
  }
  contains(target) {
    if (target === this) return true;
    return this.children.some((child) => child.contains(target));
  }
  querySelector() { return null; }
}

const selectors = [
  "#ai-provider-panel", "#ai-provider-form", "#ai-provider-status", "#ai-provider-note",
  "#ai-provider-error", "#ai-provider-result", "#ai-provider-endpoint", "#ai-provider-key",
  "#ai-provider-model", "#ai-provider-vision", "#ai-provider-fallbacks", "#ai-provider-timeout",
  "#ai-provider-proxy",
  "#ai-provider-models", "#ai-provider-save", "#ai-provider-fetch", "#ai-provider-test",
  "#ai-provider-clear-key", "#ai-protocol-chat", "#ai-protocol-responses", "#ai-chat-list",
  "#ai-chat-empty", "#ai-chat-error", "#ai-chat-state", "#ai-chat-summary", "#ai-chat-refresh",
  "#ai-vision-provider-panel", "#ai-vision-provider-form", "#ai-vision-provider-status",
  "#ai-vision-provider-note", "#ai-vision-provider-error", "#ai-vision-bench-result",
  "#ai-vision-provider-endpoint", "#ai-vision-provider-key", "#ai-vision-provider-model",
  "#ai-vision-provider-fallbacks", "#ai-vision-provider-models", "#ai-vision-provider-save",
  "#ai-vision-provider-fetch", "#ai-vision-provider-bench", "#ai-vision-provider-clear-key",
  "#ai-vision-protocol-inherit", "#ai-vision-protocol-chat", "#ai-vision-protocol-responses",
];

function harness() {
  const elements = new Map(selectors.map((selector) => [selector, new Element()]));
  for (const selector of ["#ai-provider-panel", "#ai-provider-form"]) {
    elements.get(selector).children = [];
  }
  const requests = [];
  const timers = new Map();
  let timerID = 0;
  let responder = async () => ({ ok: true, status: 200, json: async () => ({}) });
  const context = vm.createContext({
    console, Date, AbortSignal, Math, JSON, Number, String, Array, Object, Boolean,
    backendConnected: true,
    refreshBackend: () => { context.refreshed += 1; },
    refreshed: 0,
    document: {
      activeElement: null,
      querySelector: (selector) => {
        assert.ok(elements.has(selector), `Missing fixture element ${selector}`);
        return elements.get(selector);
      },
      createElement: (tag) => new Element(tag),
      addEventListener() {},
    },
    setTimeout: (handler, delay) => {
      timerID += 1;
      timers.set(timerID, { handler, delay });
      return timerID;
    },
    clearTimeout: (id) => timers.delete(id),
    fetch: async (url, options = {}) => {
      requests.push({ url, options });
      return responder(url, options);
    },
  });
  new vm.Script(source, { filename: "ai-provider.js" }).runInContext(context);
  return {
    context,
    elements,
    requests,
    timers,
    el: (selector) => elements.get(selector),
    respond(handler) { responder = handler; },
    form() { return elements.get("#ai-provider-form"); },
  };
}

function jsonHandler(routes) {
  return async (url, options = {}) => {
    const key = `${options.method || "GET"} ${url}`;
    const reply = routes[key];
    assert.ok(reply, `unexpected request ${key}`);
    if (typeof reply === "function") return reply(url, options);
    return {
      ok: (reply.status ?? 200) < 300,
      status: reply.status ?? 200,
      json: async () => reply.body ?? {},
    };
  };
}

const providerStatus = {
  configured: true, editable: true, source: "console",
  endpoint: "https://api.example.com/v1", protocol: "responses",
  model: "text-model", model_fallbacks: ["backup-model"],
  vision_model: "vision-model", timeout: 240, has_key: true, key_source: "console",
};

test("payload keeps vision optional and omits an empty key", () => {
  const { context } = harness();
  const payload = context.aiProviderPayload({
    endpoint: " https://api.example.com/v1 ", model: "text-model",
    visionModel: "", fallbacks: "", timeout: "240", key: "", protocol: "responses",
  });
  assert.deepEqual(JSON.parse(JSON.stringify(payload)), {
    endpoint: "https://api.example.com/v1", protocol: "responses", model: "text-model",
    vision_model: "", timeout: 240, model_fallbacks: [], proxy: "",
  });
  assert.equal("api_key" in payload, false);
  const withKey = context.aiProviderPayload({ key: " secret-key ", protocol: "chat" });
  assert.equal(withKey.api_key, "secret-key");
  assert.equal(withKey.protocol, "chat");
  const cleared = context.aiProviderPayload({ clearKey: true });
  assert.equal(cleared.clear_key, true);
  const proxied = context.aiProviderPayload({ proxy: " http://127.0.0.1:7890 " });
  assert.equal(proxied.proxy, "http://127.0.0.1:7890");
});

test("proxy field round-trips through the panel", async () => {
  const page = harness();
  page.context.backendConnected = true;
  page.respond(jsonHandler({
    "GET /api/backend/ai/provider": { body: { ...providerStatus, proxy: "http://127.0.0.1:7890" } },
  }));
  await page.context.refreshAIProvider(true);
  assert.equal(page.el("#ai-provider-proxy").value, "http://127.0.0.1:7890");
  assert.equal(page.el("#ai-provider-proxy").disabled, false);
  page.respond(jsonHandler({
    "GET /api/backend/ai/provider": { body: { ...providerStatus, proxy: "" } },
  }));
  await page.context.refreshAIProvider(true);
  assert.equal(page.el("#ai-provider-proxy").value, "");
});

test("payload rejects malformed fallback lists", () => {
  const { context } = harness();
  assert.throws(() => context.aiProviderPayload({ fallbacks: "not-json" }), /JSON/);
  assert.throws(() => context.aiProviderPayload({ fallbacks: '{"a":1}' }), /JSON/);
  assert.throws(() => context.aiProviderPayload({ fallbacks: "[1,2]" }), /JSON/);
  assert.deepEqual(
    JSON.parse(JSON.stringify(context.aiProviderPayload({ fallbacks: '["backup"]' }).model_fallbacks)),
    ["backup"],
  );
});

test("loading the panel disables controls until the backend connects", () => {
  const { context, el } = harness();
  context.backendConnected = false;
  return context.refreshAIProvider(true).then(() => {
    assert.equal(el("#ai-provider-endpoint").disabled, true);
    assert.equal(el("#ai-provider-save").disabled, true);
    assert.match(el("#ai-provider-status").textContent, /等待后端连接/);
  });
});

test("saving sends the protocol, model and key and refreshes the console", async () => {
  const page = harness();
  page.respond(jsonHandler({
    "GET /api/backend/ai/provider": { body: { ...providerStatus, configured: false, has_key: false, source: "" } },
    "PUT /api/backend/ai/provider": { body: providerStatus },
  }));
  await page.context.refreshAIProvider(true);
  page.el("#ai-provider-endpoint").value = "https://api.example.com/v1";
  page.el("#ai-provider-model").value = "text-model";
  page.el("#ai-provider-vision").value = "";
  page.el("#ai-provider-fallbacks").value = "";
  page.el("#ai-provider-key").value = "secret-key";
  page.el("#ai-provider-timeout").value = "240";
  page.el("#ai-provider-proxy").value = "http://127.0.0.1:7890";
  page.el("#ai-protocol-responses").fire("click");
  page.form().fire("submit");
  await new Promise((resolve) => setImmediate(resolve));
  const put = page.requests.find((request) => request.options.method === "PUT");
  assert.ok(put, "expected a PUT request");
  assert.deepEqual(JSON.parse(put.options.body), {
    endpoint: "https://api.example.com/v1", protocol: "responses", model: "text-model",
    vision_model: "", timeout: 240, proxy: "http://127.0.0.1:7890",
    model_fallbacks: [], api_key: "secret-key",
  });
  assert.equal(page.el("#ai-provider-key").value, "");
  assert.match(page.el("#ai-provider-result").textContent, /已保存/);
  assert.match(page.el("#ai-provider-status").textContent, /Responses API/);
  assert.equal(page.el("#ai-protocol-responses").getAttribute("aria-pressed"), "true");
  assert.equal(page.context.refreshed, 1);
});

test("model list fills the datalist and reports the count", async () => {
  const page = harness();
  page.respond(jsonHandler({
    "GET /api/backend/ai/provider": { body: providerStatus },
    "GET /api/backend/ai/models": { body: { models: [{ id: "text-model" }, { id: "vision-model" }] } },
  }));
  await page.context.refreshAIProvider(true);
  page.el("#ai-provider-fetch").fire("click");
  await new Promise((resolve) => setImmediate(resolve));
  const options = page.el("#ai-provider-models").children;
  assert.deepEqual(options.map((option) => option.value), ["text-model", "vision-model"]);
  assert.match(page.el("#ai-provider-result").textContent, /已获取 2 个模型/);
});

test("single question test shows the reply and maps backend failure codes", async () => {
  const page = harness();
  let fail = false;
  page.respond(async (url, options = {}) => {
    if (url.endsWith("/ai/test")) {
      if (fail) {
        return { ok: false, status: 400, json: async () => ({ error: "AI_PROVIDER_TEST_FAILED" }) };
      }
      return {
        ok: true, status: 200,
        json: async () => ({ ok: true, model: "text-model", reply: '{"Answer":["A"]}', elapsed_ms: 1250 }),
      };
    }
    return { ok: true, status: 200, json: async () => providerStatus };
  });
  await page.context.refreshAIProvider(true);
  page.el("#ai-provider-test").fire("click");
  await new Promise((resolve) => setImmediate(resolve));
  assert.match(page.el("#ai-provider-result").textContent, /测试成功 · text-model · 1\.3 秒/);
  assert.match(page.el("#ai-provider-result").textContent, /Answer/);
  fail = true;
  page.el("#ai-provider-test").fire("click");
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(page.el("#ai-provider-error").hidden, false);
  assert.match(page.el("#ai-provider-error").textContent, /单题测试失败/);
});

test("conversation rows render prompt, reply, model and failure text", () => {
  const page = harness();
  const row = page.context.renderAIChatItem({
    id: 7, created_at: 1761000000, channel: "api", protocol: "responses", model: "text-model",
    route: "text", stage: "", prompt: "QUESTION_DATA: 1 + 1 = ?", response: '{"Answer":["A"]}',
    prompt_chars: 22, response_chars: 15, images: 0, status: "ok", error: "", elapsed_ms: 1200,
    job_id: "a".repeat(32), truncated: 0,
  });
  assert.equal(row.dataset.status, "ok");
  const text = [];
  const walk = (node) => {
    if (node.textContent) text.push(node.textContent);
    for (const child of node.children) walk(child);
  };
  walk(row);
  assert.ok(text.includes("API AI · Responses API"));
  assert.ok(text.includes("text-model"));
  assert.ok(text.includes("QUESTION_DATA: 1 + 1 = ?"));
  assert.ok(text.includes('{"Answer":["A"]}'));
  assert.ok(text.some((line) => line.includes("文字") && line.includes("1.2 秒")));
  const failed = page.context.renderAIChatItem({
    id: 8, channel: "web", protocol: "chat", model: "deepseek-search", status: "error",
    error: "AI_RATE_LIMITED", prompt: "q", response: "", created_at: 1761000001,
  });
  assert.equal(failed.dataset.status, "error");
  const failureText = [];
  const walkFailed = (node) => {
    if (node.textContent) failureText.push(node.textContent);
    for (const child of node.children) walkFailed(child);
  };
  walkFailed(failed);
  assert.ok(failureText.includes("网页 AI · Chat Completions"));
  assert.ok(failureText.includes("失败"));
  assert.ok(failureText.includes("AI_RATE_LIMITED"));
});

test("opening the conversation view polls newest exchanges once", async () => {
  const page = harness();
  page.respond(async (url) => {
    if (url.includes("/ai/conversations")) {
      return {
        ok: true, status: 200,
        json: async () => ({
          conversations: [
            { id: 3, channel: "web", protocol: "chat", model: "m3", status: "ok", prompt: "q3", response: "a3", created_at: 3 },
            { id: 2, channel: "web", protocol: "chat", model: "m2", status: "ok", prompt: "q2", response: "a2", created_at: 2 },
          ],
          latest: 3,
        }),
      };
    }
    return { ok: true, status: 200, json: async () => providerStatus };
  });
  page.context.aiChatViewChanged("ai-chat");
  await new Promise((resolve) => setImmediate(resolve));
  const list = page.el("#ai-chat-list");
  assert.equal(list.children.length, 2);
  assert.equal(list.children[0].dataset.status, "ok");
  const models = (node) => {
    const found = [];
    const walkNode = (current) => {
      if (current.className === "ai-chat-model") found.push(current.textContent);
      for (const child of current.children) walkNode(child);
    };
    walkNode(node);
    return found;
  };
  assert.deepEqual(models(list.children[0]), ["m3"], "newest exchange must stay first");
  assert.deepEqual(models(list.children[1]), ["m2"]);
  assert.equal(page.el("#ai-chat-empty").hidden, true);
  assert.equal(page.el("#ai-chat-state").textContent, "实时更新");
  assert.ok(page.requests.some((request) => request.url === "/api/backend/ai/conversations?limit=40"));
  page.context.aiChatViewChanged("overview");
  assert.equal(page.timers.size, 0);
});

test("image provider payload only sends what was actually filled in", () => {
  const { context } = harness();
  const payload = context.aiVisionProviderPayload({ model: " vision-model " });
  assert.deepEqual(JSON.parse(JSON.stringify(payload)), { model: "vision-model", model_fallbacks: [] });
  assert.equal("endpoint" in payload, false);
  assert.equal("protocol" in payload, false);
  const full = context.aiVisionProviderPayload({
    model: "vision-model", endpoint: " https://api.example.com/v1 ", protocol: "responses",
    fallbacks: '["backup"]', key: " secret ",
  });
  assert.deepEqual(JSON.parse(JSON.stringify(full)), {
    model: "vision-model", endpoint: "https://api.example.com/v1", protocol: "responses",
    model_fallbacks: ["backup"], api_key: "secret",
  });
  assert.throws(() => context.aiVisionProviderPayload({ model: "" }), /图片模型必须填写/);
  assert.throws(() => context.aiVisionProviderPayload({ model: "m", fallbacks: "[1]" }), /JSON/);
});

test("image provider panel shows inheritance and saves a lone model", async () => {
  const page = harness();
  page.respond(jsonHandler({
    "GET /api/backend/ai/vision-provider": { body: {
      configured: false, inherited: true, source: "inherited", endpoint: "https://api.example.com/v1",
      protocol: "responses", model: "text-model", model_fallbacks: [], has_key: true,
    } },
    "PUT /api/backend/ai/vision-provider": { body: {
      configured: true, inherited: false, source: "console", endpoint: "https://api.example.com/v1",
      protocol: "responses", model: "vision-model", model_fallbacks: [], has_key: true,
    } },
  }));
  await page.context.refreshVisionProvider(true);
  assert.match(page.el("#ai-vision-provider-status").textContent, /未单独配置/);
  assert.equal(page.el("#ai-vision-provider-endpoint").value, "");
  assert.equal(page.el("#ai-vision-provider-model").value, "");
  assert.equal(page.el("#ai-vision-provider-key").placeholder, "留空沿用文本 API 的密钥");

  page.el("#ai-vision-provider-model").value = "vision-model";
  page.el("#ai-vision-provider-form").fire("submit");
  await new Promise((resolve) => setImmediate(resolve));
  const put = page.requests.find((request) => request.options.method === "PUT");
  assert.ok(put, "expected a PUT request");
  assert.equal(put.url, "/api/backend/ai/vision-provider");
  assert.deepEqual(JSON.parse(put.options.body), { model: "vision-model", model_fallbacks: [] });
  assert.match(page.el("#ai-vision-provider-status").textContent, /已单独配置/);
  assert.equal(page.context.refreshed, 1);
});

test("accuracy bench shows one verdict per question", async () => {
  const page = harness();
  page.respond(jsonHandler({
    "GET /api/backend/ai/vision-provider": { body: {
      configured: true, inherited: false, source: "console", model: "vision-model", has_key: true,
    } },
    "POST /api/backend/ai/vision-bench": { body: {
      mode: "direct", available: 2, graded: 2, correct: 1, accuracy: 0.5, model: "vision-model",
      items: [
        { id: "aaaaaaaa", type: "single", images: 5, expected: "B", got: "B", ok: true, seconds: 12.5, error: "" },
        { id: "bbbbbbbb", type: "single", images: 5, expected: "C", got: "A", ok: false, seconds: 9.1, error: "" },
      ],
    } },
  }));
  await page.context.refreshVisionProvider(true);
  page.el("#ai-vision-provider-bench").fire("click");
  await new Promise((resolve) => setImmediate(resolve));
  const list = page.el("#ai-vision-bench-result");
  assert.equal(list.hidden, false);
  assert.equal(list.children.length, 3);
  assert.match(list.children[0].textContent, /答对 1 \/ 2 题（正确率 50%）/);
  assert.equal(list.children[0].dataset.state, "warn");
  assert.equal(list.children[1].dataset.state, "ok");
  assert.equal(list.children[2].dataset.state, "error");
  assert.match(list.children[2].textContent, /标准 C · 实测 A/);
  const post = page.requests.find((request) => request.options.method === "POST");
  assert.deepEqual(JSON.parse(post.options.body), { limit: 6, mode: "direct" });
});

test("conversation polling surfaces backend failures", async () => {
  const page = harness();
  page.respond(async (url) => {
    if (url.includes("/ai/conversations")) {
      return { ok: false, status: 502, json: async () => ({ error: "后端连接失败或超时" }) };
    }
    return { ok: true, status: 200, json: async () => providerStatus };
  });
  page.context.aiChatViewChanged("ai-chat");
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(page.el("#ai-chat-state").textContent, "更新失败");
  assert.match(page.el("#ai-chat-error").textContent, /AI 对话读取失败/);
});
