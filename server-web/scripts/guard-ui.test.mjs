import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = fs.readFileSync(new URL("../internal/server/static/guard.js", import.meta.url), "utf8");
const html = fs.readFileSync(new URL("../internal/server/static/index.html", import.meta.url), "utf8");

class Element {
  constructor(tag = "div") {
    this.tag = tag;
    this.children = [];
    this.listeners = new Map();
    this.dataset = {};
    this.value = "";
    this.textContent = "";
    this.hidden = false;
    this.disabled = false;
    this.checked = false;
  }
  addEventListener(name, handler) {
    const handlers = this.listeners.get(name) || [];
    handlers.push(handler);
    this.listeners.set(name, handlers);
  }
  fire(name) {
    for (const handler of this.listeners.get(name) || []) handler({ target: this, currentTarget: this });
  }
  setAttribute() {}
  getAttribute() { return null; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  querySelector() { return null; }
}

const guardIDs = [
  "guard-state", "guard-note", "guard-error", "guard-hint",
  "guard-max-speed", "guard-quiet-hours", "guard-rollback-pause", "guard-recheck",
  "guard-save", "guard-refresh", "guard-rollback-list", "guard-rollback-empty",
];

function harness(handler) {
  const elements = new Map(guardIDs.map((id) => ["#" + id, new Element()]));
  const requests = [];
  const context = vm.createContext({
    console, Date, AbortSignal, Number, JSON, Math, String, Boolean,
    document: {
      querySelector: (selector) => elements.get(selector) || null,
      createElement: (tag) => new Element(tag),
    },
    fetch: async (url, options = {}) => {
      requests.push({ url, method: options.method || "GET", body: options.body });
      return handler(url, options);
    },
  });
  vm.runInContext(source, context);
  const element = (id) => elements.get("#" + id);
  return { element, requests, run: (code) => vm.runInContext(code, context) };
}

function ok(body) {
  return { ok: true, status: 200, json: async () => body };
}

const snapshot = {
  max_speed: 1.2, quiet_hours: true, auto_pause: true, recheck: 6,
  rollbacks: {
    count: 2,
    items: [{
      job_id: "job-1", created_at: 1792200000, course: "军事理论", name: "1.2 中国国防的历史",
      from_position: 620, to_position: 0, dropped: 620, action: "slow",
    }],
  },
};

test("the pacing page hosts the guard controls and loads its script after pacing", () => {
  for (const id of guardIDs.slice(0, 4)) assert.match(html, new RegExp(`id="${id}"`));
  assert.match(html, /id="pacing-view"[\s\S]*id="guard-title"[\s\S]*id="guard-recheck"/);
  assert.match(html, /<script src="pacing.js"><\/script>\s*<script src="guard.js"><\/script>/);
});

test("loading renders the stored guard settings and recent rollbacks", async () => {
  const f = harness(() => ok(snapshot));
  await f.run(`loadVideoGuard()`);
  assert.equal(f.requests[0].url, "/api/backend/video/guard");
  assert.equal(f.element("guard-max-speed").value, "1.2");
  assert.equal(f.element("guard-quiet-hours").checked, true);
  assert.equal(f.element("guard-rollback-pause").checked, true);
  assert.equal(f.element("guard-recheck").value, "6");
  assert.equal(f.element("guard-state").textContent, "近 7 天回退 2 次");
  assert.equal(f.element("guard-state").dataset.state, "ok");
  assert.match(f.element("guard-note").textContent, /最多 1\.2 倍速/);
  assert.match(f.element("guard-note").textContent, /回退后自动暂停/);
  const rows = f.element("guard-rollback-list").children;
  assert.equal(rows.length, 1);
  assert.equal(rows[0].dataset.action, "slow");
  assert.match(rows[0].children[0].textContent, /军事理论/);
  assert.match(rows[0].children[1].textContent, /10 分 20 秒 → 0 秒（少 10 分 20 秒）/);
  assert.match(rows[0].children[1].textContent, /已降到 1 倍速/);
  assert.equal(f.element("guard-rollback-empty").hidden, true);
});

test("an empty history says so instead of showing an empty list", async () => {
  const f = harness(() => ok({ ...snapshot, rollbacks: { count: 0, items: [] } }));
  await f.run(`loadVideoGuard()`);
  assert.equal(f.element("guard-rollback-list").children.length, 0);
  assert.equal(f.element("guard-rollback-empty").hidden, false);
  assert.equal(f.element("guard-state").textContent, "近 7 天无回退");
});

test("saving posts every guard field once and confirms the change", async () => {
  const f = harness(() => ok(snapshot));
  await f.run(`loadVideoGuard()`);
  f.element("guard-max-speed").value = "1.5";
  f.element("guard-quiet-hours").checked = false;
  f.element("guard-rollback-pause").checked = true;
  f.element("guard-recheck").value = "12";
  await f.run(`saveVideoGuard()`);
  const put = f.requests.find((request) => request.method === "PUT");
  assert.equal(put.url, "/api/backend/video/guard");
  assert.deepEqual(JSON.parse(put.body), {
    max_speed: 1.5, quiet_hours: false, auto_pause: true, recheck: 12,
  });
  assert.equal(f.requests.filter((request) => request.method === "PUT").length, 1);
  assert.match(f.element("guard-hint").textContent, /已保存/);
  assert.equal(f.element("guard-error").hidden, true);
});

test("out of range values are refused before any request is sent", async () => {
  const f = harness(() => ok(snapshot));
  f.element("guard-max-speed").value = "2.4";
  f.element("guard-recheck").value = "8";
  await f.run(`saveVideoGuard()`);
  assert.equal(f.requests.length, 0);
  assert.match(f.element("guard-error").textContent, /倍速上限要在 0\.5 到 1\.5 之间/);

  f.element("guard-max-speed").value = "1.5";
  f.element("guard-recheck").value = "0";
  await f.run(`saveVideoGuard()`);
  assert.equal(f.requests.length, 0);
  assert.match(f.element("guard-error").textContent, /回读间隔要是 1 到 50 之间的整数/);
});

test("a rejected save keeps the values and shows the server message", async () => {
  const f = harness(() => ({ ok: false, status: 400, json: async () => ({ error: "Invalid maximum speed" }) }));
  f.element("guard-max-speed").value = "1.5";
  f.element("guard-recheck").value = "8";
  await f.run(`saveVideoGuard()`);
  assert.match(f.element("guard-error").textContent, /Invalid maximum speed/);
  assert.equal(f.element("guard-hint").textContent, "");
});

test("an unreachable backend is reported without breaking the page", async () => {
  const f = harness(() => { throw new Error("network down"); });
  await f.run(`loadVideoGuard()`);
  assert.equal(f.element("guard-state").dataset.state, "unknown");
  assert.match(f.element("guard-error").textContent, /读取看护设置失败/);
});

test("only the pacing page triggers the guard read", async () => {
  const f = harness(() => ok(snapshot));
  f.run(`guardViewChanged("tasks")`);
  assert.equal(f.requests.length, 0);
  f.run(`guardViewChanged("pacing")`);
  assert.equal(f.requests.length, 1);
});
