import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = fs.readFileSync(new URL("../internal/server/static/backend.js", import.meta.url), "utf8");
const accountA = "a".repeat(32);
const accountB = "b".repeat(32);
const readySummary = {
  status: "ready", point_count: 1, task_count: 1, completed_count: 0, remaining_count: 1, locked_count: 0,
};
const courses = [
  { id: "101", title: "Offline course one", teacher: "Teacher", summary: readySummary },
  { id: "202", title: "Offline course two", teacher: "", summary: { ...readySummary } },
];

class Element {
  constructor(tag = "div") {
    this.tag = tag;
    this.children = [];
    this.listeners = new Map();
    this.attributes = new Map();
    this.dataset = {};
    this.value = "";
    this.textContent = "";
    this.hidden = false;
    this.disabled = false;
    this.checked = false;
    this.open = false;
    this.isConnected = true;
    this.parentElement = null;
    this.scrollTop = 0;
    this.clientHeight = 0;
    this.classList = {
      names: new Set(),
      add: (...names) => names.forEach((name) => this.classList.names.add(name)),
      remove: (...names) => names.forEach((name) => this.classList.names.delete(name)),
      contains: (name) => this.classList.names.has(name),
      toggle: (name, force) => {
        const on = force === undefined ? !this.classList.names.has(name) : Boolean(force);
        if (on) this.classList.names.add(name); else this.classList.names.delete(name);
        return on;
      },
    };
  }
  addEventListener(name, handler) {
    const handlers = this.listeners.get(name) || [];
    handlers.push(handler);
    this.listeners.set(name, handlers);
  }
  fire(name) {
    const event = {
      target: this, currentTarget: this, defaultPrevented: false,
      preventDefault() { this.defaultPrevented = true; },
    };
    for (const handler of this.listeners.get(name) || []) {
      handler(event);
    }
    if (name === "cancel" && !event.defaultPrevented) this.close();
    return event;
  }
  setAttribute(name, value) { this.attributes.set(name, value); }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  removeAttribute(name) { this.attributes.delete(name); }
  append(...children) {
    for (const child of children) this.insertBefore(child, null);
  }
  insertBefore(child, before) {
    child.remove();
    const index = before ? this.children.indexOf(before) : this.children.length;
    assert.ok(index >= 0);
    this.children.splice(index, 0, child);
    child.parentElement = this;
    child.isConnected = this.isConnected;
  }
  remove() {
    if (this.parentElement) {
      const siblings = this.parentElement.children;
      siblings.splice(siblings.indexOf(this), 1);
      this.parentElement = null;
    }
    this.isConnected = false;
  }
  replaceChildren(...children) {
    for (const child of [...this.children]) child.remove();
    this.append(...children);
    if (this.tag === "select") this.value = children[0]?.value || "";
  }
  querySelectorAll(selector) {
    const matches = [];
    for (const child of this.children) {
      if (child.tag === selector || (selector === "button[type=submit]" && child.tag === "button" && child.type === "submit")) {
        matches.push(child);
      }
      matches.push(...child.querySelectorAll(selector));
    }
    return matches;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  reset() {}
  showModal() { this.open = true; }
  close() { this.open = false; this.fire("close"); }
  focus() { this.focused = true; }
  select() { this.selected = true; }
}

async function settle() {
  for (let index = 0; index < 5; index += 1) await new Promise(setImmediate);
}

async function harness() {
  const selectors = [
    "connection-form", "backend-url", "backend-token", "backend-error", "backend-status",
    "backend-controls", "account-form", "account-target", "job-account",
    "job-account-input", "job-account-options", "job-account-combobox", "job-form",
    "job-dialog", "add-job", "job-form-error", "job-connection-error",
    "job-editor-close", "job-editor-cancel", "create-job-label",
    "fetch-courses", "course-list", "course-status", "course-error", "select-incomplete-courses", "clear-course-selection", "only-incomplete-courses",
    "course-detail-panel", "course-detail-title", "course-detail-status", "course-detail-summary",
    "course-detail-error", "course-detail-list", "refresh-course-detail",
    "course-detail-selection", "select-detail-points", "clear-detail-points",
    "create-job", "jobs", "overview-jobs", "overview-jobs-empty", "overview-jobs-summary",
    "overview-tab", "overview-view", "overview-backend-state", "overview-backend-note",
    "overview-ai-state", "overview-ai-note", "overview-refresh", "overview-open-tasks",
    "tasks-tab", "archived-tab", "archived-view", "archived-jobs", "archived-search",
    "archived-refresh", "archived-summary", "archived-error", "archived-empty",
    "notes-tab", "tasks-view", "notes-view",
    "count", "disconnect-backend", "jobs-empty", "account-password", "account-label",
    "account-username", "videos-only", "auto-submit", "experimental-vision", "human-pacing", "connection-panel", "course-selection-count",
    "job-search", "job-course-filter", "filter-all", "filter-active", "filter-pacing",
    "filter-attention", "filter-finished",
    "ai-channel-web", "ai-channel-api", "ai-channel-status", "ai-channel-note", "ai-channel-error",
    "ai-vision-auto", "ai-vision-web", "ai-vision-api", "ai-vision-enabled", "ai-vision-fallback", "ai-vision-direct",
    "metric-running", "metric-queued", "metric-ai", "metric-pending", "metric-capacity",
    "metric-ai-status", "metric-attention", "sync-status", "sync-message", "last-sync",
    "database-status", "view-title", "dashboard-summary",
    "accounts-tab", "accounts-view", "manage-accounts", "account-dialog", "account-delete-dialog",
    "accounts-list", "account-search", "account-filter", "accounts-refresh", "add-account",
    "accounts-total", "accounts-studying", "accounts-completed", "accounts-attention",
    "accounts-empty", "accounts-empty-title", "accounts-status", "account-editor-title",
    "account-form-error", "save-account", "account-editor-close", "account-editor-cancel",
    "account-delete-title", "account-delete-message", "account-delete-error", "account-delete-close",
    "account-delete-cancel", "confirm-delete-account",
    "open-job-batch", "job-batch-dialog", "job-batch-form", "job-batch-action",
    "job-batch-search", "job-batch-list", "job-batch-confirm", "job-batch-note",
    "job-batch-count", "job-batch-select-all", "job-batch-clear", "job-batch-empty",
    "job-batch-confirm-label", "job-batch-confirm-text", "job-batch-result",
    "job-batch-error", "job-batch-submit", "job-batch-submit-label", "job-batch-close",
    "job-batch-cancel", "select-visible-jobs", "job-selection-count",
    "clear-job-selection", "quick-resume-jobs", "quick-pause-jobs",
  ];
  const elements = new Map(selectors.map((id) => ["#" + id, new Element()]));
  const element = (id) => elements.get("#" + id);
  for (const id of ["connection-form", "account-form", "job-form"]) element(id).tag = "form";
  for (const id of ["fetch-courses", "create-job", "select-incomplete-courses", "clear-course-selection"]) element(id).tag = "button";
  for (const id of ["overview-refresh", "overview-open-tasks"]) element(id).tag = "button";
  element("only-incomplete-courses").checked = true;
  element("job-course-filter").tag = "select";
  element("archived-view").hidden = true;
  element("job-batch-action").value = "resume";
  element("refresh-course-detail").tag = "button";
  const connectButton = new Element("button");
  connectButton.type = "submit";
  element("connection-form").append(connectButton);
  element("save-account").tag = "button";
  element("save-account").type = "submit";
  element("account-form").append(element("save-account"));
  element("create-job").type = "submit";
  element("job-form").append(element("fetch-courses"), element("create-job"));
  element("auto-submit").checked = true;
  element("human-pacing").checked = true;
  element("jobs-empty").append(new Element("h3"), new Element("p"));
  element("overview-jobs-empty").append(new Element("h3"), new Element("p"));
  element("archived-empty").append(new Element("h3"), new Element("p"));
  const timers = new Map();
  const requests = [];
  const fixture = { element, requests, timers };
  fixture.route = (url) => {
    if (url === "/api/backend") return { configured: false };
    if (url.endsWith("/courses")) return {
      account_id: element("job-account").value, state: "idle", request_id: null, courses: [],
    };
    if (url.endsWith("/health")) return { status: "ok", jobs: {}, max_workers: 30 };
    if (url.endsWith("/accounts")) return { accounts: [] };
    return { jobs: [] };
  };
  let timerID = 0;
  const context = vm.createContext({
    console, Date, AbortSignal, crypto: { randomUUID },
    noteCount: 0,
    document: {
      hidden: false,
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
      requests.push({ url, ...options });
      const result = await fixture.route(url, options);
      const status = Number.isInteger(result.status) ? result.status : 200;
      return { ok: status < 400, status,
        json: async () => result.body || result };
    },
  });
  fixture.run = (code) => vm.runInContext(code, context);
  vm.runInContext(source, context);
  await settle();
  fixture.run(`backendConnected = true; accountsFresh = true; jobAccount.value = "${accountA}"; resetCourses();`);
  fixture.catalog = (account = accountA, list = courses) => ({
    account_id: account, request_id: "request-" + account, state: "done",
    courses: list, updated_at: 1700000000,
  });
  return fixture;
}

function accountRecord(overrides = {}) {
  return {
    id: accountA, label: "Offline account", username_masked: "13***02", revision: 1,
    completed_courses: 3, last_completed_at: 1792200000, is_studying: false,
    current_job: null, can_delete: true, delete_blocked_reason: "", ...overrides,
  };
}

async function showAccounts(f, accounts) {
  f.run(`accountsFresh = true; renderAccounts(${JSON.stringify(accounts)});`);
  await settle();
}

test("account management is an independent navigation page outside the course form", async () => {
  const html = fs.readFileSync(new URL("../internal/server/static/index.html", import.meta.url), "utf8");
  const consoleUI = fs.readFileSync(new URL("../internal/server/static/console-ui.js", import.meta.url), "utf8");
  assert.match(html, /id="accounts-tab"[^>]*aria-controls="accounts-view"/);
  assert.match(consoleUI, /accounts: \{ title: "账号管理", section: "workspace" \}/);
  const tasks = html.slice(html.indexOf('<section id="tasks-view"'), html.indexOf('<section id="accounts-view"'));
  assert.doesNotMatch(tasks, /id="account-form"|id="account-panel"/);
  assert.match(tasks, /id="job-account"/);
  const f = await harness();
  f.element("accounts-tab").fire("click");
  assert.equal(f.element("accounts-view").hidden, false);
  assert.equal(f.element("tasks-view").hidden, true);
  assert.equal(f.element("count").textContent, "0 个账号");
});

test("account cards show whole-course totals and live activity instead of chapter counts", async () => {
  const f = await harness();
  await showAccounts(f, [accountRecord({
    is_studying: true, can_delete: false, delete_blocked_reason: "ACCOUNT_HAS_ACTIVE_JOB",
    current_job: { id: "job", status: "running", progress: {
      course: "Offline course", completed: 200, total: 500,
      phase: "video", name: "Offline.mp4", position: 120, duration: 300, video_state: "playing",
    } },
  }), accountRecord({ id: accountB, label: "Idle account", completed_courses: 5 })]);
  assert.equal(f.element("accounts-list").children.length, 2);
  assert.equal(f.element("accounts-completed").textContent, "8");
  assert.equal(f.element("accounts-studying").textContent, "1");
  const view = f.run(`accountViews.get("${accountA}")`);
  assert.equal(view.count.textContent, "3");
  assert.equal(view.state.textContent, "正在刷课");
  assert.match(view.activity.textContent, /Offline\.mp4.*02:00 \/ 05:00/);
  assert.equal(view.remove.disabled, true);
  assert.match(view.remove.title, /取消或删除/);
});

test("account refresh preserves card nodes, selected account and unfinished editor input", async () => {
  const f = await harness();
  const initial = accountRecord();
  await showAccounts(f, [initial]);
  const card = f.element("accounts-list").children[0];
  const selectedText = f.element("job-account-input").value;
  f.run(`openAccountEditor(remoteAccounts[0])`);
  f.element("account-label").value = "Unsaved label";
  f.element("account-password").value = "unsaved-password";
  await showAccounts(f, [{ ...initial, completed_courses: 4 }]);
  assert.equal(f.element("accounts-list").children[0], card);
  assert.equal(f.element("job-account-input").value, selectedText);
  assert.equal(f.element("account-label").value, "Unsaved label");
  assert.equal(f.element("account-password").value, "unsaved-password");
  assert.equal(f.element("account-dialog").open, true);
});

test("account search and status filters do not modify course selection or fetch courses", async () => {
  const f = await harness();
  await showAccounts(f, [
    accountRecord({ label: "Alpha", is_studying: true, current_job: { status: "running" } }),
    accountRecord({ id: accountB, label: "Beta" }),
  ]);
  f.run('selectedCourseIDs = new Set(["101"]);');
  const before = f.requests.length;
  f.element("account-filter").value = "studying";
  f.element("account-filter").fire("change");
  assert.equal(f.element("accounts-list").children.length, 1);
  f.element("account-search").value = "Beta";
  f.element("account-search").fire("input");
  assert.equal(f.element("accounts-list").children.length, 0);
  assert.equal(f.element("accounts-empty-title").textContent, "没有匹配的账号");
  assert.equal(f.run("selectedCourseIDs.has('101')"), true);
  assert.equal(f.requests.length, before);
});

test("task account picker searches by account text and selects without a native dropdown", async () => {
  const f = await harness();
  f.route = (url) => {
    const match = url.match(/accounts\/([a-f0-9]{32})\/courses/);
    return f.catalog(match?.[1] || accountA);
  };
  await showAccounts(f, [
    accountRecord({ label: "Alpha", username_masked: "13***01" }),
    accountRecord({ id: accountB, label: "Beta", username_masked: "18***02" }),
  ]);
  assert.match(f.element("job-account-input").value, /Alpha/);
  assert.equal(f.element("job-account-input").disabled, false);
  f.element("job-account-input").fire("focus");
  assert.equal(f.element("job-account-options").hidden, false);
  assert.equal(f.element("job-account-options").querySelectorAll("button").length, 2);

  f.element("job-account-input").value = "beta";
  f.element("job-account-input").fire("input");
  const options = f.element("job-account-options").querySelectorAll("button");
  assert.equal(options.length, 1);
  assert.match(options[0].children[0].textContent, /Beta/);
  options[0].fire("click");
  await settle();
  assert.equal(f.element("job-account").value, accountB);
  assert.match(f.element("job-account-input").value, /Beta/);
  assert.equal(f.element("job-account-options").hidden, true);
  assert.ok(f.requests.some((request) => request.url === `/api/backend/accounts/${accountB}/courses`));
});

test("opening an account selects its course list without starting jobs or new platform logins", async () => {
  const f = await harness();
  await showAccounts(f, [accountRecord(), accountRecord({ id: accountB })]);
  f.requests.length = 0;
  f.run(`accountViews.get("${accountB}").open.fire("click")`);
  await settle();
  assert.equal(f.element("job-account").value, accountB);
  assert.equal(f.element("tasks-view").hidden, false);
  assert.equal(f.element("accounts-view").hidden, true);
  assert.equal(f.element("job-dialog").open, true);
  assert.ok(f.requests.some((request) => request.url.includes(`/accounts/${accountB}/courses`)));
  assert.ok(f.requests.every((request) => !request.method || request.method === "GET"));
});

test("account deletion requires confirmation and removes only the selected account", async () => {
  const f = await harness();
  const first = accountRecord();
  const second = accountRecord({ id: accountB, label: "Other account" });
  await showAccounts(f, [first, second]);
  let deleted = false;
  f.route = (url, options) => {
    if (options.method === "DELETE") {
      assert.equal(url, `/api/backend/accounts/${accountA}`);
      deleted = true;
      return { status: 204 };
    }
    if (url.endsWith("/accounts")) return { accounts: deleted ? [second] : [first, second] };
    if (url.endsWith("/health")) return { status: "ok" };
    if (url.endsWith("/courses")) return f.catalog(accountB);
    return { jobs: [] };
  };
  f.run(`accountViews.get("${accountA}").remove.fire("click")`);
  assert.equal(f.element("account-delete-dialog").open, true);
  assert.equal(f.element("account-delete-cancel").focused, true);
  assert.match(f.element("account-delete-message").textContent, /共享题库不受影响/);
  assert.equal(deleted, false);
  f.element("confirm-delete-account").fire("click");
  await settle();
  assert.equal(deleted, true);
  assert.equal(f.element("account-delete-dialog").open, false);
  assert.equal(f.run(`remoteAccounts.some((item) => item.id === "${accountA}")`), false);
  assert.equal(f.run(`remoteAccounts.some((item) => item.id === "${accountB}")`), true);
});

test("busy accounts and accounts becoming active during confirmation cannot be deleted", async () => {
  const f = await harness();
  const first = accountRecord();
  await showAccounts(f, [first]);
  f.run(`openAccountDelete("${accountA}")`);
  await showAccounts(f, [{ ...first, can_delete: false, delete_blocked_reason: "ACCOUNT_HAS_ACTIVE_JOB",
    current_job: { status: "running" } }]);
  assert.equal(f.element("confirm-delete-account").disabled, true);
  f.element("confirm-delete-account").fire("click");
  await settle();
  assert.equal(f.requests.filter((request) => request.method === "DELETE").length, 0);
  f.element("account-delete-cancel").fire("click");
  f.run(`accountViews.get("${accountA}").remove.fire("click")`);
  assert.equal(f.element("account-delete-dialog").open, false);
});

test("a rejected deletion keeps the account and surfaces the server protection message", async () => {
  const f = await harness();
  await showAccounts(f, [accountRecord()]);
  f.route = () => ({ status: 409, body: {
    error: "Cancel or delete this account's jobs before deleting the account",
  } });
  f.run(`openAccountDelete("${accountA}")`);
  f.element("confirm-delete-account").fire("click");
  await settle();
  assert.equal(f.element("account-delete-dialog").open, true);
  assert.equal(f.element("account-delete-error").hidden, false);
  assert.match(f.element("account-delete-error").textContent, /先取消或删除/);
  assert.equal(f.element("accounts-list").children.length, 1);
});

test("duplicate delete clicks send one request and cannot dismiss the pending confirmation", async () => {
  const f = await harness();
  await showAccounts(f, [accountRecord()]);
  let release;
  f.route = () => new Promise((resolve) => { release = resolve; });
  f.run(`openAccountDelete("${accountA}")`);
  f.element("confirm-delete-account").fire("click");
  f.element("confirm-delete-account").fire("click");
  assert.equal(f.requests.filter((request) => request.method === "DELETE").length, 1);
  f.element("account-delete-cancel").fire("click");
  assert.equal(f.element("account-delete-dialog").open, true);
  release({ status: 409, body: { error: "Protected" } });
  await settle();
  assert.equal(f.element("account-delete-cancel").disabled, false);
});

test("a late pre-deletion poll cannot restore a deleted account", async () => {
  const f = await harness();
  const first = accountRecord();
  await showAccounts(f, [first]);
  let release;
  f.route = (url, options) => {
    if (options.method === "DELETE") return { status: 204 };
    if (url.endsWith("/accounts")) return new Promise((resolve) => { release = resolve; });
    if (url.endsWith("/health")) return { status: "ok" };
    return { jobs: [] };
  };
  const pending = f.run("refreshBackend()");
  await settle();
  f.run(`openAccountDelete("${accountA}")`);
  f.element("confirm-delete-account").fire("click");
  await settle();
  release({ accounts: [first] });
  await pending;
  assert.equal(f.run("remoteAccounts.length"), 0);
  assert.equal(f.element("accounts-list").children.length, 0);
});

test("editing can preserve credentials, and closing clears the password", async () => {
  const f = await harness();
  const account = accountRecord();
  await showAccounts(f, [account]);
  f.run("openAccountEditor(remoteAccounts[0])");
  assert.equal(f.element("account-username").required, false);
  assert.equal(f.element("account-password").required, false);
  f.element("account-label").value = "New label";
  let submitted;
  f.route = (url, options) => {
    if (options.method === "PUT") {
      submitted = JSON.parse(options.body);
      return account;
    }
    if (url.endsWith("/accounts")) return { accounts: [{ ...account, label: "New label", revision: 2 }] };
    if (url.endsWith("/health")) return { status: "ok" };
    if (url.endsWith("/courses")) return f.catalog();
    return { jobs: [] };
  };
  f.element("account-form").fire("submit");
  await settle();
  assert.deepEqual(submitted, { label: "New label", username: "", password: "" });
  assert.equal(f.element("account-dialog").open, false);
  f.run("openAccountEditor()");
  assert.equal(f.element("account-username").required, true);
  f.element("account-password").value = "private";
  f.element("account-editor-cancel").fire("click");
  assert.equal(f.element("account-password").value, "");
});

test("stale account data keeps existing cards but disables destructive actions", async () => {
  const f = await harness();
  await showAccounts(f, [accountRecord()]);
  const card = f.element("accounts-list").children[0];
  f.route = () => { throw new Error("Offline"); };
  await f.run("refreshBackend()");
  assert.equal(f.element("accounts-list").children[0], card);
  assert.match(f.element("accounts-status").textContent, /保留上次数据/);
  assert.equal(f.run(`accountViews.get("${accountA}").remove.disabled`), true);
  assert.equal(f.element("add-account").disabled, true);
});

test("fetching courses polls asynchronously, does not create jobs, and requires explicit selection", async () => {
  const f = await harness();
  f.route = (url, options) => {
    if (options.method === "POST") return {
      account_id: accountA, request_id: "request-" + accountA, state: "queued", courses: [],
    };
    return f.catalog();
  };
  await f.run("loadCourses(true)");
  assert.equal(f.element("fetch-courses").disabled, true);
  assert.equal(f.element("create-job").disabled, true);
  assert.equal(f.timers.size, 1);
  await f.run("loadCourses()");
  assert.equal(f.element("course-list").querySelectorAll("input").length, 2);
  assert.equal(f.element("fetch-courses").disabled, false);
  assert.equal(f.element("create-job").disabled, true);
  assert.equal(f.requests.filter((request) => request.url.endsWith("/jobs")).length, 0);
  const checkbox = f.element("course-list").querySelectorAll("input")[0];
  checkbox.checked = true;
  checkbox.fire("change");
  assert.equal(f.element("create-job").disabled, false);
  assert.match(f.element("course-selection-count").textContent, /已选 1/);
});

test("job creation submits only checked IDs and does not activate the fetch button", async () => {
  const f = await harness();
  let submitted;
  f.route = (url, options) => {
    if (url.endsWith("/courses")) return f.catalog();
    if (url.endsWith("/jobs") && options.method === "POST") {
      submitted = JSON.parse(options.body);
      return { id: "offline-job" };
    }
    if (url.endsWith("/accounts")) return { accounts: [{ id: accountA, label: "Offline", revision: 1 }] };
    if (url.endsWith("/health")) return { status: "ok", max_workers: 30 };
    return { jobs: [] };
  };
  await f.run("loadCourses()");
  const checkbox = f.element("course-list").querySelectorAll("input")[1];
  checkbox.checked = true;
  checkbox.fire("change");
  f.element("add-job").fire("click");
  f.element("filter-finished").fire("click");
  f.element("job-search").value = "old filter";
  f.element("job-form").fire("submit");
  await settle();
  assert.deepEqual(submitted.course_ids, ["202"]);
  assert.equal(submitted.account_id, accountA);
  assert.equal(submitted.videos_only, false);
  assert.equal(submitted.submit, true);
  assert.equal(submitted.preview, false);
  assert.equal(submitted.experimental_vision, false);
  assert.equal(submitted.human_pacing, true);
  assert.equal(f.element("job-dialog").open, false);
  assert.equal(f.run("selectedCourseIDs.size"), 0);
  assert.equal(f.run("jobFilter"), "all");
  assert.equal(f.element("job-search").value, "");
  assert.equal(f.requests.filter((request) => request.url.endsWith("/courses") && request.method === "POST").length, 0);
});

test("watch mode submits videos_only and disables quiz submission", async () => {
  const f = await harness();
  let submitted;
  f.route = (url, options) => {
    if (url.endsWith("/courses")) return f.catalog();
    if (url.endsWith("/jobs") && options.method === "POST") {
      submitted = JSON.parse(options.body);
      return { id: "watch-only-job" };
    }
    if (url.endsWith("/accounts")) return { accounts: [{ id: accountA, label: "Offline", revision: 1 }] };
    if (url.endsWith("/health")) return { status: "ok", max_workers: 30 };
    return { jobs: [] };
  };
  await f.run("loadCourses()");
  f.element("course-list").querySelectorAll("input")[0].checked = true;
  f.element("course-list").querySelectorAll("input")[0].fire("change");
  f.element("videos-only").checked = true;
  f.element("videos-only").fire("change");
  f.element("job-form").fire("submit");
  await settle();
  assert.equal(submitted.videos_only, true);
  assert.equal(submitted.submit, false);
  assert.equal(submitted.preview, false);
});

test("empty selection never sends a job request", async () => {
  const f = await harness();
  f.element("job-form").fire("submit");
  await settle();
  assert.equal(f.requests.filter((request) => request.url.endsWith("/jobs")).length, 0);
  assert.equal(f.element("job-form-error").hidden, false);
});

test("task cards fill the task page and task creation lives in an accessible dialog", () => {
  const html = fs.readFileSync(new URL("../internal/server/static/index.html", import.meta.url), "utf8");
  const css = fs.readFileSync(new URL("../internal/server/static/app.css", import.meta.url), "utf8");
  const tasks = html.slice(html.indexOf('<section id="tasks-view"'), html.indexOf('<dialog id="job-dialog"'));
  const dialog = html.slice(html.indexOf('<dialog id="job-dialog"'), html.indexOf('<section id="accounts-view"'));
  assert.match(tasks, /id="add-job"[^>]*aria-haspopup="dialog"[^>]*aria-controls="job-dialog"/);
  assert.match(tasks, /id="jobs" class="job-grid"/);
  assert.doesNotMatch(tasks, /id="job-form"|class="workspace-grid"|class="selection-pane"/);
  assert.match(dialog, /aria-labelledby="job-editor-title"/);
  assert.match(dialog, /id="job-form"/);
  assert.match(dialog, /id="job-form-error"[^>]*role="alert"/);
  assert.match(css, /\.job-grid \{[^}]*repeat\(2, minmax\(0, 1fr\)\)/);
  assert.match(css, /\.job-dialog-body \{[^}]*overflow-y: auto/);
});

test("closing and reopening task creation preserves the selected courses, settings and list nodes", async () => {
  const f = await harness();
  f.route = () => f.catalog();
  await f.run("loadCourses()");
  const checkbox = f.element("course-list").querySelectorAll("input")[0];
  checkbox.checked = true;
  checkbox.fire("change");
  f.element("videos-only").checked = true;
  f.element("videos-only").fire("change");
  f.element("experimental-vision").checked = true;
  f.element("course-list").scrollTop = 125;
  const requests = f.requests.length;
  f.element("add-job").fire("click");
  assert.equal(f.element("job-dialog").open, true);
  assert.equal(f.element("job-account-input").focused, true);
  f.element("job-editor-cancel").fire("click");
  assert.equal(f.element("job-dialog").open, false);
  f.element("add-job").fire("click");
  assert.equal(f.element("job-dialog").open, true);
  assert.equal(f.element("course-list").querySelectorAll("input")[0], checkbox);
  assert.equal(checkbox.checked, true);
  assert.equal(f.element("course-list").scrollTop, 125);
  assert.equal(f.element("videos-only").checked, true);
  assert.equal(f.element("auto-submit").checked, false);
  assert.equal(f.element("auto-submit").disabled, true);
  assert.equal(f.element("experimental-vision").checked, true);
  assert.equal(f.requests.length, requests);
  const cancel = f.element("job-dialog").fire("cancel");
  assert.equal(cancel.defaultPrevented, false);
  assert.equal(f.element("job-dialog").open, false);
});

test("the manage accounts command closes task creation without discarding its draft", async () => {
  const f = await harness();
  f.route = () => f.catalog();
  await f.run("loadCourses()");
  f.element("select-incomplete-courses").fire("click");
  f.element("add-job").fire("click");
  f.element("manage-accounts").fire("click");
  assert.equal(f.element("job-dialog").open, false);
  assert.equal(f.element("accounts-view").hidden, false);
  assert.equal(f.element("tasks-view").hidden, true);
  assert.equal(f.run("selectedCourseIDs.size"), 2);
});

test("a rejected task remains open with its draft and its own error despite background refresh", async () => {
  const f = await harness();
  const account = accountRecord();
  f.route = (url, options) => {
    if (url.endsWith("/courses")) return f.catalog();
    if (url.endsWith("/jobs") && options.method === "POST") return { status: 409, body: { error: "Account already has an active job" } };
    if (url.endsWith("/accounts")) return { accounts: [account] };
    if (url.endsWith("/health")) return { status: "ok", max_workers: 30 };
    return { jobs: [] };
  };
  await showAccounts(f, [account]);
  f.element("select-incomplete-courses").fire("click");
  f.element("add-job").fire("click");
  f.element("job-form").fire("submit");
  await settle();
  assert.equal(f.element("job-dialog").open, true);
  assert.equal(f.element("job-form-error").hidden, false);
  assert.match(f.element("job-form-error").textContent, /active job/);
  assert.equal(f.element("job-form-error").focused, true);
  assert.equal(f.element("create-job").disabled, false);
  assert.equal(f.run("selectedCourseIDs.size"), 2);
  await f.run("refreshBackend()");
  assert.equal(f.element("job-form-error").hidden, false);
  assert.equal(f.run("selectedCourseIDs.size"), 2);
});

test("pending task submission locks its controls and prevents cancellation or duplicate requests", async () => {
  const f = await harness();
  let release;
  f.route = (url, options) => {
    if (url.endsWith("/courses")) return f.catalog();
    if (url.endsWith("/jobs") && options.method === "POST") return new Promise((resolve) => { release = resolve; });
    return { jobs: [] };
  };
  await f.run("loadCourses()");
  f.element("select-incomplete-courses").fire("click");
  f.element("add-job").fire("click");
  f.element("job-form").fire("submit");
  for (const id of ["job-account", "fetch-courses", "select-incomplete-courses",
    "clear-course-selection", "videos-only", "auto-submit", "experimental-vision", "manage-accounts",
    "job-editor-close", "job-editor-cancel", "create-job"]) {
    assert.equal(f.element(id).disabled, true, id);
  }
  assert.equal(f.element("job-form").attributes.get("aria-busy"), "true");
  f.element("job-editor-cancel").fire("click");
  assert.equal(f.element("job-dialog").open, true);
  assert.equal(f.element("job-dialog").fire("cancel").defaultPrevented, true);
  f.element("job-form").fire("submit");
  assert.equal(f.requests.filter((request) => request.url.endsWith("/jobs") && request.method === "POST").length, 1);
  release({ status: 503, body: { error: "Temporarily unavailable" } });
  await settle();
  assert.equal(f.element("job-dialog").open, true);
  assert.equal(f.element("job-editor-cancel").disabled, false);
  assert.equal(f.element("job-form").attributes.get("aria-busy"), "false");
  assert.equal(f.run("selectedCourseIDs.size"), 2);
});

test("a connection interruption preserves the open draft, disables submissions and recovers in place", async () => {
  const f = await harness();
  const account = accountRecord();
  const online = (url) => {
    if (url.endsWith("/courses")) return f.catalog();
    if (url.endsWith("/accounts")) return { accounts: [account] };
    if (url.endsWith("/health")) return { status: "ok", max_workers: 30 };
    return { jobs: [] };
  };
  f.route = online;
  await showAccounts(f, [account]);
  f.element("select-incomplete-courses").fire("click");
  f.element("add-job").fire("click");
  const checkbox = f.element("course-list").querySelectorAll("input")[0];
  f.route = () => { throw new Error("Offline"); };
  await f.run("refreshBackend()");
  assert.equal(f.element("job-dialog").open, true);
  assert.equal(f.element("job-connection-error").hidden, false);
  assert.equal(f.element("create-job").disabled, true);
  assert.equal(f.element("add-job").disabled, true);
  assert.equal(f.run("selectedCourseIDs.size"), 2);
  f.element("job-form").fire("submit");
  await settle();
  assert.equal(f.requests.filter((request) => request.method === "POST").length, 0);
  f.route = online;
  await f.run("refreshBackend()");
  assert.equal(f.element("job-dialog").open, true);
  assert.equal(f.element("job-connection-error").hidden, true);
  assert.equal(f.element("create-job").disabled, false);
  assert.equal(f.element("course-list").querySelectorAll("input")[0], checkbox);
  assert.equal(checkbox.checked, true);
});

test("a late task submission response cannot close a new connection's draft or reset its selection", async () => {
  const f = await harness();
  let release;
  f.route = (url, options) => {
    if (url.endsWith("/courses")) return f.catalog();
    if (url.endsWith("/jobs") && options.method === "POST") return new Promise((resolve) => { release = resolve; });
    return { jobs: [] };
  };
  await f.run("loadCourses()");
  f.element("select-incomplete-courses").fire("click");
  f.element("add-job").fire("click");
  f.element("job-form").fire("submit");
  f.run(`connectionGeneration += 1; resetCourses(); selectedCourseIDs = new Set(["new-draft"]);`);
  release({ id: "late-job" });
  await settle();
  assert.equal(f.element("job-dialog").open, true);
  assert.equal(f.run("selectedCourseIDs.has('new-draft')"), true);
  assert.equal(f.requests.filter((request) => request.url.endsWith("/health")).length, 0);
});

test("repeated submit while a job request is pending does not create a second request", async () => {
  const f = await harness();
  let release;
  f.route = (url, options) => {
    if (url.endsWith("/courses")) return f.catalog();
    if (url.endsWith("/jobs") && options.method === "POST") {
      return new Promise((resolve) => { release = resolve; });
    }
    if (url.endsWith("/accounts")) return { accounts: [] };
    return { status: "ok", jobs: [] };
  };
  await f.run("loadCourses()");
  f.element("select-incomplete-courses").fire("click");
  f.element("job-form").fire("submit");
  f.element("job-form").fire("submit");
  assert.equal(f.requests.filter((request) => request.url.endsWith("/jobs") && request.method === "POST").length, 1);
  release({ id: "offline-job" });
  await settle();
});

test("account switch ignores a late response from the previous account", async () => {
  const f = await harness();
  let release;
  f.route = () => new Promise((resolve) => { release = resolve; });
  const pending = f.run("loadCourses(true)");
  f.run(`jobAccount.value = "${accountB}"; resetCourses();`);
  f.route = () => f.catalog(accountB, [{ id: "303", title: "Other account course", teacher: "", summary: { ...readySummary } }]);
  await f.run("loadCourses()");
  release(f.catalog());
  await pending;
  assert.deepEqual(f.element("course-list").querySelectorAll("input").map((input) => input.value), ["303"]);
  assert.equal(f.element("create-job").disabled, true);
});

test("connection switch ignores late course results and clears selection", async () => {
  const f = await harness();
  let release;
  f.route = () => new Promise((resolve) => { release = resolve; });
  const pending = f.run("loadCourses(true)");
  f.run("connectionGeneration += 1; backendConnected = false; resetCourses();");
  release(f.catalog());
  await pending;
  assert.equal(f.element("course-list").children.length, 0);
  assert.equal(f.element("fetch-courses").disabled, true);
});

test("background account refresh preserves selection, but credential revision changes clear it", async () => {
  const f = await harness();
  const accounts = [{ id: accountA, label: "Offline", username_masked: "***", revision: 1 }];
  f.route = () => f.catalog();
  f.run(`renderAccounts(${JSON.stringify(accounts)})`);
  await settle();
  const checkbox = f.element("course-list").querySelectorAll("input")[0];
  checkbox.checked = true;
  checkbox.fire("change");
  f.run(`renderAccounts(${JSON.stringify(accounts)})`);
  assert.equal(f.element("create-job").disabled, false);
  assert.equal(f.element("course-list").querySelectorAll("input")[0].checked, true);
  f.route = () => ({ account_id: accountA, request_id: null, state: "idle", courses: [] });
  f.run(`renderAccounts(${JSON.stringify([{ ...accounts[0], revision: 2 }])})`);
  await settle();
  assert.equal(f.element("course-list").children.length, 0);
  assert.equal(f.element("create-job").disabled, true);
});

test("select incomplete chooses only learnable courses and stops at 50", async () => {
  const f = await harness();
  f.route = () => f.catalog(accountA, Array.from({ length: 51 }, (_, index) => ({
    id: String(index + 1), title: `Offline ${index + 1}`, teacher: "", summary: { ...readySummary },
  })));
  await f.run("loadCourses()");
  f.element("select-incomplete-courses").fire("click");
  assert.equal(f.element("course-list").querySelectorAll("input").filter((input) => input.checked).length, 50);
  assert.equal(f.element("create-job").disabled, false);
  assert.match(f.element("course-error").textContent, /50/);
  f.element("clear-course-selection").fire("click");
  assert.equal(f.element("course-list").querySelectorAll("input").filter((input) => input.checked).length, 0);
  assert.equal(f.element("create-job").disabled, true);
});

test("login errors and empty course lists remain retryable and never enable job creation", async () => {
  const f = await harness();
  f.route = () => ({ ...f.catalog(), state: "error", error_code: "LOGIN_REQUIRED", courses: [] });
  await f.run("loadCourses(true)");
  assert.equal(f.element("course-error").hidden, false);
  assert.equal(f.element("fetch-courses").disabled, false);
  assert.equal(f.element("create-job").disabled, true);
  f.route = () => f.catalog(accountA, []);
  await f.run("loadCourses(true)");
  assert.equal(f.element("course-error").hidden, true);
  assert.equal(f.element("select-incomplete-courses").disabled, true);
  assert.equal(f.element("create-job").disabled, true);
});

test("course rows open task-point details with completion and remaining counts", async () => {
  const f = await harness();
  const detail = {
    account_id: accountA, course_id: "101", request_id: "detail-101", state: "done",
    detail: {
      course: courses[0],
      points: [
        { id: "9001", title: "第一章视频", task_count: 2, completed_count: 1,
          remaining_count: 1, completed: false, locked: false },
        { id: "9002", title: "第二章测验", task_count: 1, completed_count: 1,
          remaining_count: 0, completed: true, locked: false },
      ],
      totals: { task_count: 3, completed_count: 2, remaining_count: 1, point_count: 2 },
    },
  };
  f.route = (url, options) => {
    if (url.endsWith("/courses")) return f.catalog();
    if (url.includes("/courses/")) return detail;
    return { jobs: [] };
  };
  await f.run("loadCourses()");
  const detailButton = f.element("course-list").querySelectorAll("button")[0];
  detailButton.fire("click");
  await settle();
  assert.equal(f.element("course-detail-panel").hidden, false);
  assert.match(f.element("course-detail-summary").textContent, /已完成 2/);
  assert.match(f.element("course-detail-summary").textContent, /剩余 1/);
  const text = (node) => [node.textContent, ...node.children.flatMap(text)].join(" ");
  assert.match(text(f.element("course-detail-list")), /第一章视频/);
  assert.match(text(f.element("course-detail-list")), /剩余 1/);
});

test("single task points can be picked and only those are submitted", async () => {
  const f = await harness();
  let submitted;
  const detail = {
    account_id: accountA, course_id: "101", request_id: "detail-101", state: "done",
    detail: {
      course: courses[0],
      points: [
        { id: "9001", title: "第一章视频", task_count: 2, completed_count: 1,
          remaining_count: 1, completed: false, locked: false },
        { id: "9002", title: "第二章测验", task_count: 1, completed_count: 1,
          remaining_count: 0, completed: true, locked: false },
        { id: "9003", title: "第三章锁定", task_count: 1, completed_count: 0,
          remaining_count: 1, completed: false, locked: true },
      ],
      totals: { task_count: 4, completed_count: 2, remaining_count: 2, point_count: 3 },
    },
  };
  f.route = (url, options) => {
    if (url.endsWith("/courses")) return f.catalog();
    if (url.includes("/courses/")) return detail;
    if (url.endsWith("/jobs") && options.method === "POST") {
      submitted = JSON.parse(options.body);
      return { id: "point-job" };
    }
    return { jobs: [] };
  };
  await f.run("loadCourses()");
  f.element("course-list").querySelectorAll("button")[0].fire("click");
  await settle();
  const rows = () => f.element("course-detail-list").children;
  assert.equal(rows()[2].children[0].disabled, true, "locked chapter cannot be picked");
  assert.match(f.element("course-detail-selection").textContent, /整门课都学/);
  const box = rows()[0].children[0];
  box.checked = true;
  box.fire("change");
  await settle();
  assert.equal(f.run("selectedCourseIDs.has('101')"), true, "picking a chapter selects the course");
  assert.match(f.element("course-detail-selection").textContent, /已勾选 1 \/ 3 节/);
  f.element("job-form").fire("submit");
  await settle();
  assert.deepEqual(submitted.course_ids, ["101"]);
  assert.deepEqual(submitted.point_ids, { 101: ["9001"] });
});

test("a finished course can still be studied chapter by chapter", async () => {
  const f = await harness();
  const done = { ...readySummary, remaining_count: 0, completed_count: 1 };
  f.route = (url) => {
    if (url.endsWith("/courses")) return f.catalog(accountA, [
      { id: "303", title: "Done course", teacher: "", summary: done },
    ]);
    if (url.includes("/courses/")) return {
      account_id: accountA, course_id: "303", request_id: "detail-303", state: "done",
      detail: {
        course: { id: "303", title: "Done course", teacher: "", summary: done },
        points: [
          { id: "7001", title: "已经看完的一节", task_count: 1, completed_count: 1,
            remaining_count: 0, completed: true, locked: false },
        ],
        totals: { task_count: 1, completed_count: 1, remaining_count: 0, point_count: 1 },
      },
    };
    return { jobs: [] };
  };
  await f.run("loadCourses()");
  // 已完成的课程默认被"仅未完成"过滤掉，取消过滤后才看得到
  f.element("only-incomplete-courses").checked = false;
  f.element("only-incomplete-courses").fire("change");
  await settle();
  assert.equal(f.element("course-list").querySelectorAll("input")[0].disabled, true);
  f.element("course-list").querySelectorAll("button")[0].fire("click");
  await settle();
  const box = f.element("course-detail-list").children[0].children[0];
  box.checked = true;
  box.fire("change");
  await settle();
  assert.equal(f.run("courseSelectable(courseItems[0])"), true);
  assert.equal(f.element("create-job").disabled, false);
  f.element("clear-detail-points").fire("click");
  await settle();
  assert.equal(f.run("selectedCourseIDs.size"), 0);
  assert.equal(f.element("create-job").disabled, true);
});

test("zero-task chapters are labeled without claiming a completed task", async () => {
  const f = await harness();
  const detail = {
    course: courses[0],
    points: [
      { id: "9001", title: "Empty", task_count: 0, completed_count: 0,
        remaining_count: 0, completed: false, locked: false },
      { id: "9002", title: "Remaining", task_count: 2, completed_count: 0,
        remaining_count: 2, completed: false, locked: false },
    ],
    totals: { task_count: 2, completed_count: 0, remaining_count: 2, point_count: 2 },
  };
  f.run(`renderCourseDetail({ detail: ${JSON.stringify(detail)} })`);
  const text = (node) => [node.textContent, ...node.children.flatMap(text)].join(" ");
  assert.match(f.element("course-detail-summary").textContent, /共 2 节 · 任务点 2/);
  assert.match(text(f.element("course-detail-list").children[0]), /无任务点/);
  assert.doesNotMatch(text(f.element("course-detail-list").children[0]), /已完成/);
  f.run(`liveCourseDetails.set("${accountA}/101", { result: { detail: ${JSON.stringify(detail)} } });
    const view = createLiveOutline();
    updateLiveOutline(view, { id: "empty-job", account_id: "${accountA}", progress: {} }, "101");
    globalThis.emptyOutline = view;`);
  assert.match(text(f.run("emptyOutline.list.children[0]")), /无任务点/);
  assert.doesNotMatch(text(f.run("emptyOutline.list.children[0]")), /已完成/);
});

test("courses containing only zero-task chapters remain unselectable", async () => {
  const f = await harness();
  f.route = () => f.catalog(accountA, [{
    id: "101", title: "No task points", teacher: "",
    summary: { ...readySummary, point_count: 3, task_count: 0, remaining_count: 0 },
  }]);
  await f.run("loadCourses()");
  assert.equal(f.element("course-list").querySelectorAll("input").length, 0);
  f.element("only-incomplete-courses").checked = false;
  f.element("only-incomplete-courses").fire("change");
  assert.equal(f.element("course-list").querySelectorAll("input")[0].disabled, true);
  assert.match(f.element("course-selection-count").textContent, /已完成 0 · 无任务点 1/);
  assert.equal(f.run("courseSummaryText(courseItems[0])"), "无任务点");
  f.element("select-incomplete-courses").fire("click");
  assert.equal(f.element("create-job").disabled, true);
});

test("job cards render video progress and AI answer status details", async () => {
  const f = await harness();
  const videoJob = f.run(`renderJob({
    id: "offline-job", label: "Offline course", status: "running", course_ids: ["101"],
    updated_at: 1700000000,
    progress: {
      course: "Offline course", chapter: "2.4.7 CPR", phase: "video",
      name: "3.2.6 CPR.mp4", position: 120, duration: 890,
      completed: 4, total: 10,
    },
  })`);
  const aiJob = f.run(`renderJob({
    id: "offline-ai-job", label: "Offline course", status: "running", course_ids: ["101"],
    updated_at: 1700000000,
    progress: {
      course: "Offline course", chapter: "2.4.7 CPR", phase: "workid",
      completed: 4, total: 10, ai_state: "running", ai_type: "judgement",
      ai_question: "人体的心脏停搏、呼吸骤停是最不紧急的情况。",
    },
  })`);
  const text = (node) => [node.textContent, ...node.children.flatMap(text)].join(" ");
  const videoText = text(videoJob);
  const aiText = text(aiJob);
  assert.match(videoText, /Offline course/);
  assert.match(videoText, /当前视频/);
  assert.match(videoText, /视频进度/);
  assert.match(videoText, /02:00 \/ 14:50/);
  assert.match(aiText, /AI 正在处理/);
  assert.match(aiText, /判断题/);
  assert.match(aiText, /人体的心脏停搏/);
});

test("cancelled job cards expose an explicit delete action", async () => {
  const f = await harness();
  const cancelled = f.run(`renderJob({
    id: "offline-cancelled", label: "Offline course", status: "cancelled", course_ids: ["101"],
    updated_at: 1700000000, progress: { completed: 1, total: 2 },
  })`);
  const buttons = cancelled.querySelectorAll("button").map((button) => button.textContent);
  assert.ok(buttons.includes("删除"));
});

test("image question progress distinguishes downloads, visual AI waiting and missing vision", async () => {
  const f = await harness();
  for (const [state, label] of [
    ["downloading_images", "正在读取题目图片"], ["vision_queued", "图片题等待识图 AI"],
    ["vision_running", "AI 正在识图答题"], ["vision_unavailable", "识图 AI 未就绪"],
    ["vision_recognizing", "豆包正在识别原图"], ["vision_solving", "DeepSeek 正在解题"],
    ["vision_verifying", "豆包正在独立复核原图"],
  ]) {
    f.run(`renderJob({
      id: "vision-job", label: "Image practice", status: "running", course_ids: ["101"],
      updated_at: 1700000000,
      progress: {
        phase: "workid", ai_mode: "vision", ai_image_count: 3, ai_state: "${state}",
        ai_type: "completion", ai_question: "Formula [image_1]",
      },
    })`);
    assert.equal(f.run("jobViews.get('vision-job').aiState.textContent"), `${label} · 3 张图片`);
    assert.equal(f.run("jobViews.get('vision-job').rows.aiType.content.textContent"), "填空题");
  }
});

test("completed, failed, and old courses cannot be selected", async () => {
  const f = await harness();
  const list = [
    { id: "1", title: "Finished", teacher: "", summary: { ...readySummary, completed_count: 1, remaining_count: 0 } },
    { id: "2", title: "Remaining", teacher: "", summary: { ...readySummary, remaining_count: 2, locked_count: 1 } },
    { id: "3", title: "Failed", teacher: "", summary: { ...readySummary, status: "failed", remaining_count: 0, completed_count: 0 } },
    { id: "4", title: "Cached", teacher: "" },
  ];
  f.route = () => f.catalog(accountA, list);
  await f.run("loadCourses()");
  assert.deepEqual(f.element("course-list").querySelectorAll("input").map((input) => input.value), ["2"]);
  f.element("only-incomplete-courses").checked = false;
  f.element("only-incomplete-courses").fire("change");
  const boxes = f.element("course-list").querySelectorAll("input");
  assert.deepEqual(boxes.map((input) => [input.value, input.disabled]), [
    ["1", true], ["2", false], ["3", true], ["4", true],
  ]);
  assert.match(f.element("course-selection-count").textContent, /已完成 1/);
  boxes[0].checked = true;
  boxes[0].fire("change");
  assert.equal(f.element("create-job").disabled, true);
});

test("active jobs refresh quickly and highlight the current chapter", async () => {
  const f = await harness();
  const detail = {
    state: "done", account_id: accountA, course_id: "101",
    detail: {
      course: { id: "101", title: "Offline course one" },
      points: [
        { id: "1", title: "Finished", task_count: 1, completed_count: 1, remaining_count: 0, completed: true, locked: false },
        { id: "2", title: "Current chapter", task_count: 1, completed_count: 0, remaining_count: 1, completed: false, locked: false },
      ],
      totals: { task_count: 2, completed_count: 1, remaining_count: 1, point_count: 2 },
    },
  };
  f.route = (url) => {
    if (url.endsWith("/health")) return { status: 200, body: { status: "ok", jobs: { running: 1 }, max_workers: 30, ai_workers: 1, ai_configured: true } };
    if (url.endsWith("/accounts")) return { accounts: [] };
    if (url.includes("/courses/101")) return detail;
    if (url.endsWith("/jobs")) return { jobs: [{
      id: "job-1", account_id: accountA, label: "Offline", status: "running", course_ids: ["101"],
      updated_at: 1700000000,
      progress: { course_id: "101", course: "Offline course one", chapter: "Current chapter", phase: "chapter", completed: 1, total: 2 },
    }] };
    return { jobs: [] };
  };
  await f.run("refreshBackend()");
  await settle();
  assert.ok([...f.timers.values()].some((timer) => timer.delay === 1000));
  const text = (node) => [node.textContent, ...(node.children || []).flatMap(text)].join(" ");
  const jobs = f.element("jobs");
  assert.match(text(jobs), /Current chapter/);
  assert.match(text(jobs), /正在学习/);
  const current = (node) => {
    if (String(node.className || "").includes("is-current")) return node;
    for (const child of node.children || []) {
      const found = current(child);
      if (found) return found;
    }
    return null;
  };
  assert.match(text(current(jobs)), /Current chapter/);
});

test("progress updates keep the job, action buttons, and progress elements mounted", async () => {
  const f = await harness();
  const job = {
    id: "stable-job", label: "Offline", status: "running", course_ids: ["101"],
    updated_at: 1700000000,
    progress: { phase: "video", name: "Test video", position: 1, duration: 20 },
  };
  const first = f.run(`renderJob(${JSON.stringify(job)})`);
  const pause = first.querySelectorAll("button")[0];
  const progress = first.querySelectorAll("progress")[0];
  const second = f.run(`renderJob(${JSON.stringify({
    ...job, progress: { ...job.progress, position: 2 },
  })})`);
  assert.equal(first, second);
  assert.equal(second.querySelectorAll("button")[0], pause);
  assert.equal(second.querySelectorAll("progress")[0], progress);
  assert.equal(progress.value, 2);
});

test("video-only jobs render a graphical live player and whole-course progress", async () => {
  const f = await harness();
  const job = {
    id: "watch-visual", label: "Watch course", status: "running", course_ids: ["101"],
    options: { videos_only: true },
    updated_at: 1700000000,
    progress: {
      course: "劳动教育", chapter: "劳动与劳动教育", phase: "video",
      name: "认识劳动.mp4", position: 250, duration: 600, completed: 25, total: 60,
    },
  };
  f.run(`renderJob(${JSON.stringify(job)})`);
  const view = f.run("jobViews.get('watch-visual')");
  assert.equal(view.visual.hidden, false);
  assert.equal(view.visualVideo.textContent, "认识劳动.mp4");
  assert.equal(view.visualContext.textContent, "劳动教育 · 劳动与劳动教育");
  assert.equal(view.visualPercent.textContent, "41.7%");
  assert.equal(view.visualTime.textContent, "04:10 / 10:00");
  assert.equal(view.visualTrack.max, 600);
  assert.equal(view.visualTrack.value, 250);
  assert.equal(view.overallPercent.textContent, "42%");
  assert.equal(view.overall.getAttribute("aria-valuenow"), "42");
  assert.equal(view.overall.getAttribute("style"), "--progress: 42%");
  assert.equal(view.rows.video.row.hidden, true);
  assert.equal(view.rows.videoName.row.hidden, true);
  assert.equal(view.rows.course.row.hidden, true);
  f.run(`renderJob(${JSON.stringify({ ...job, progress: { ...job.progress, phase: "chapter" } })})`);
  assert.equal(view.visual.hidden, true);
});

test("document names and unsupported task labels are shown as safe, bounded text", async () => {
  const f = await harness();
  const job = {
    id: "manual-review", label: "Offline", status: "needs_attention", course_ids: ["101"],
    updated_at: 1700000000,
    progress: {
      course_id: "101", chapter: "Chapter with manual items", phase: "chapter",
      manual_tasks: [
        { type: "hyperlink", title: "新时代主题成就展", card_num: "2", href: "https://private.example/?token=secret" },
        { type: "hyperlink", title: "<img src=x onerror=alert(1)>", card_num: "3" },
      ],
    },
  };
  f.run(`renderJob(${JSON.stringify(job)})`);
  const view = f.run("jobViews.get('manual-review')");
  assert.equal(view.manualReview.hidden, false);
  assert.equal(view.manualHeading.textContent, "待人工查看 2 项");
  assert.equal(view.manualList.children.length, 2);
  assert.equal(view.manualList.children[0].children[0].textContent, "新时代主题成就展");
  assert.match(view.manualList.children[0].children[1].textContent, /任务卡 2 · 超链接/);
  assert.equal(view.manualList.querySelectorAll("img").length, 0);
  assert.equal(view.manualReview.textContent.includes("private.example"), false);
  f.run(`renderJob(${JSON.stringify({ ...job, progress: { ...job.progress, phase: "document", name: "Lecture 01.pptx", manual_tasks: [] } })})`);
  assert.equal(view.rows.documentName.row.hidden, false);
  assert.equal(view.rows.documentName.content.textContent, "Lecture 01.pptx");
  assert.equal(view.manualReview.hidden, true);
  f.run(`renderJob(${JSON.stringify({ ...job, progress: {
    ...job.progress, phase: "course_unconfirmed", name: "需要人工处理：征程 [hyperlink]；加油！新时代[hyperlink]",
    manual_tasks: undefined,
  } })})`);
  assert.equal(view.manualHeading.textContent, "待人工查看 2 项");
  assert.match(view.manualList.children.map((item) => item.children[0].textContent).join(" "), /征程/);
});

test("attention jobs restore manual tasks from paged history with chapter context", async () => {
  const f = await harness();
  const job = {
    id: "historical-manual", label: "Offline", status: "needs_attention", course_ids: ["101"],
    updated_at: 1700000000, progress: { course_id: "101", phase: "course_unconfirmed" },
  };
  const firstPage = Array.from({ length: 200 }, (_, index) => ({
    id: index + 1, kind: "progress", data: {},
  }));
  firstPage[8] = { id: 9, kind: "phase", data: {
    course_id: "101", chapter_id: "11", chapter: "1.1 导论", manual_tasks: [
      { type: "hyperlink", title: "学习资料入口", card_num: "2", href: "https://private.example/?token=secret" },
      { type: "document", title: "课程说明.pdf", card_num: "3" },
      { type: "hyperlink", title: "拓展阅读", card_num: "4" },
    ],
  } };
  const secondPage = [{ id: 201, kind: "phase", data: {
    course_id: "101", chapter_id: "12", chapter: "1.2 训练要求", manual_tasks: [
      { type: "hyperlink", title: "训练资料一", card_num: "2" },
      { type: "hyperlink", title: "训练资料二", card_num: "3" },
      { type: "hyperlink", title: "训练资料三", card_num: "4" },
      { type: "document", title: "附件目录.docx", card_num: "5" },
    ],
  } }];
  const route = f.route;
  f.route = (url, options) => {
    if (url.includes(`/jobs/${job.id}/events`)) {
      const after = Number(new URL(url, "http://local").searchParams.get("after"));
      return { events: after === 0 ? firstPage : secondPage };
    }
    return route(url, options);
  };
  f.run(`remoteJobs = [${JSON.stringify(job)}]; renderJobs(); refreshManualTaskHistory();`);
  await settle();
  const view = f.run(`jobViews.get("${job.id}")`);
  assert.equal(view.manualHeading.textContent, "待人工查看 7 项");
  assert.equal(view.manualList.children.length, 7);
  assert.equal(view.manualList.children[0].children[0].textContent, "学习资料入口");
  assert.match(view.manualList.children[0].children[1].textContent, /1\.1 导论 · 任务卡 2 · 超链接/);
  assert.match(view.manualList.children[6].children[1].textContent, /1\.2 训练要求/);
  assert.equal(view.manualReview.textContent.includes("private.example"), false);
  assert.deepEqual(f.requests.filter((request) => request.url.includes("/events")).map((request) => request.url), [
    `/api/backend/jobs/${job.id}/events?after=0`,
    `/api/backend/jobs/${job.id}/events?after=200`,
  ]);
  f.run("refreshManualTaskHistory()");
  await settle();
  assert.equal(f.requests.filter((request) => request.url.includes("/events")).length, 2);
});

test("successful chapter history clears its former manual items", async () => {
  const f = await harness();
  const job = {
    id: "resolved-manual", label: "Offline", status: "needs_attention", course_ids: ["101"],
    updated_at: 1700000000, progress: { course_id: "101", phase: "course_unconfirmed" },
  };
  f.route = (url, options) => {
    if (url.includes(`/jobs/${job.id}/events`)) return { events: [
      { id: 1, kind: "phase", data: {
        course_id: "101", chapter_id: "11", chapter: "1.1 导论",
        manual_tasks: [{ type: "document", title: "已处理附件.pdf", card_num: "2" }],
      } },
      { id: 2, kind: "chapter_result", data: {
        course_id: "101", chapter_id: "11", chapter: "1.1 导论", result: "SUCCESS",
      } },
      { id: 3, kind: "phase", data: {
        course_id: "101", chapter_id: "12", chapter: "1.2 训练要求",
        manual_tasks: [{ type: "hyperlink", title: "仍需人工查看", card_num: "3" }],
      } },
    ] };
    return { jobs: [] };
  };
  f.run(`remoteJobs = [${JSON.stringify(job)}]; renderJobs(); refreshManualTaskHistory();`);
  await settle();
  const view = f.run(`jobViews.get("${job.id}")`);
  assert.equal(view.manualHeading.textContent, "待人工查看 1 项");
  assert.equal(view.manualList.children[0].children[0].textContent, "仍需人工查看");
});

test("collapsed courses scroll to the current chapter only after manual expansion", async () => {
  const f = await harness();
  f.run(`
    remoteJobs = [{
      id: "current-on-entry", label: "Offline", status: "running", updated_at: 1700000000,
      account_id: "${accountA}", course_ids: ["101"],
      progress: { course_id: "101", chapter_id: "24", chapter: "Current chapter" }
    }];
    liveCourseDetails.set("${accountA}/101", { attemptedAt: Date.now(), version: 0, requestedVersion: 0, result: { detail: {
      course: { id: "101", title: "Offline" }, totals: {},
      points: [{ id: "24", title: "Current chapter", task_count: 1, remaining_count: 1 }]
    }}});
    renderJobs();
    selectView("tasks");
  `);
  assert.equal(f.element("tasks-view").hidden, false);
  assert.equal(
    f.run('jobViews.get("current-on-entry").courses.entries.get("101").group.open'),
    false,
  );
  f.run(`
    const outline = jobViews.get("current-on-entry").courses.entries.get("101").outline;
    jobViews.get("current-on-entry").courses.entries.get("101").group.open = true;
    outline.list.clientHeight = 180;
    outline.list.getBoundingClientRect = () => ({ top: 100 });
    outline.current.clientHeight = 20;
    outline.current.getBoundingClientRect = () => ({ top: 500 });
    renderJobs();
  `);
  assert.equal(f.run('jobViews.get("current-on-entry").courses.entries.get("101").outline.list.scrollTop'), 320);
  assert.equal(f.requests.filter((request) => request.url.includes("/courses/")).length, 0);
});

test("reaching the video end is never displayed as a confirmed task without acknowledgement", async () => {
  const f = await harness();
  const job = {
    id: "confirming-job", label: "Offline", status: "running", course_ids: ["101"],
    updated_at: 1700000000,
    progress: { phase: "video", name: "Test", position: 20, duration: 20, video_state: "confirming" },
  };
  f.run(`renderJob(${JSON.stringify(job)})`);
  assert.match(f.run("jobViews.get('confirming-job').rows.videoState.content.textContent"), /等待平台确认/);
  f.run(`renderJob(${JSON.stringify({ ...job, progress: { ...job.progress, video_state: "passed" } })})`);
  assert.match(f.run("jobViews.get('confirming-job').rows.videoState.content.textContent"), /平台已确认通过/);
});

test("a slow outline never blocks fast progress polling and never starts a second login", async () => {
  const f = await harness();
  let release;
  f.route = (url) => {
    if (url.includes("/courses/101")) return new Promise((resolve) => { release = resolve; });
    if (url.endsWith("/health")) return { status: 200, body: { status: "ok", max_workers: 30 } };
    if (url.endsWith("/accounts")) return { accounts: [] };
    return { jobs: [{
      id: "slow-outline", account_id: accountA, label: "Offline", status: "running",
      course_ids: ["101"], updated_at: 1700000000,
      progress: { course_id: "101", phase: "video", position: 1, duration: 20 },
    }] };
  };
  await f.run("refreshBackend()");
  assert.equal(f.element("jobs").children.length, 1);
  assert.ok([...f.timers.values()].some((timer) => timer.delay === 1000));
  assert.ok(f.requests.filter((request) => request.url.includes("/courses/101"))
    .every((request) => !request.method || request.method === "GET"));
  release({ account_id: accountA, course_id: "101", state: "idle", detail: null });
  await settle();
});

test("late outline responses update only their own course without replacing another course", async () => {
  const f = await harness();
  let release;
  const detail = (id) => ({
    account_id: accountA, course_id: id, state: "done",
    detail: { course: { id, title: id }, points: [], totals: {} },
  });
  f.route = (url) => url.includes("/courses/101")
    ? new Promise((resolve) => { release = resolve; }) : detail("202");
  const old = f.run(`refreshLiveCourse({
    id: "one", account_id: "${accountA}", progress: { course_id: "101" }
  })`);
  await f.run(`refreshLiveCourse({
    id: "two", account_id: "${accountA}", progress: { course_id: "202" }
  })`);
  release(detail("101"));
  await old;
  assert.equal(f.run(`liveCourseDetails.get("${accountA}/202").result.course_id`), "202");
  assert.equal(f.run(`liveCourseDetails.get("${accountA}/101").result.course_id`), "101");
});

test("new worker snapshots refresh the outline without waiting for the periodic timer", async () => {
  const f = await harness();
  f.route = () => ({
    account_id: accountA, course_id: "101", state: "done",
    detail: { course: { id: "101" }, points: [], totals: {} },
  });
  const job = { id: "snapshot", account_id: accountA, progress: { course_id: "101", outline_updated_at: 1 } };
  await f.run(`refreshLiveCourse(${JSON.stringify(job)})`);
  const initial = f.requests.length;
  await f.run(`refreshLiveCourse(${JSON.stringify(job)})`);
  assert.equal(f.requests.length, initial);
  await f.run(`refreshLiveCourse(${JSON.stringify({
    ...job, progress: { ...job.progress, outline_updated_at: 2 },
  })})`);
  assert.equal(f.requests.length, initial + 1);
});

function cachedOutline(account = accountA, courseID = "101", overrides = {}) {
  return {
    account_id: account, course_id: courseID, state: "done", updated_at: 1700000000,
    detail: {
      course: { id: courseID, title: `Course ${courseID}` },
      points: [
        { id: "1", title: "Finished", task_count: 1, completed_count: 1, remaining_count: 0, completed: true },
        { id: "2", title: `Current ${courseID}`, task_count: 1, completed_count: 0, remaining_count: 1, completed: false },
      ],
      totals: { task_count: 2, completed_count: 1, remaining_count: 1, point_count: 2 },
    }, ...overrides,
  };
}

test("each task lists all selected courses in order with full titles and independent completion", async () => {
  const f = await harness();
  const titles = ["A complete course name that must never be truncated", "Current full course name", "Waiting full course name"];
  const list = ["101", "202", "303"].map((id, index) => ({
    id, title: titles[index], summary: { ...readySummary, completed_count: index === 0 ? 1 : 0,
      remaining_count: index === 0 ? 0 : 1 },
  }));
  f.route = (url) => url.endsWith("/courses") ? f.catalog(accountA, list) : cachedOutline();
  await f.run(`readLiveCourseResource("${accountA}")`);
  const job = {
    id: "multi", account_id: accountA, label: "Multi-course", status: "running",
    course_ids: ["101", "202", "303"], updated_at: 1700000000,
    progress: { course_id: "202", course: titles[1], chapter_id: "2" },
  };
  f.run(`renderJob(${JSON.stringify(job)})`);
  const view = f.run("jobViews.get('multi').courses");
  assert.deepEqual([...view.entries.keys()], ["101", "202", "303"]);
  assert.deepEqual([...view.entries.values()].map((entry) => entry.title.textContent), titles);
  assert.deepEqual([...view.entries.values()].map((entry) => entry.state.textContent), ["已完成", "正在学习", "待学习"]);
  assert.equal(view.count.textContent, "共 3 门 · 已完成 1 门");
  assert.equal(view.entries.get("202").group.open, false);
  assert.equal(view.entries.get("101").group.open, false);
  assert.equal(view.entries.get("303").group.open, false);
  assert.equal(f.requests.filter((request) => request.method && request.method !== "GET").length, 0);
});

test("all running task cards get their own course lists and current chapter, not only the latest job", async () => {
  const f = await harness();
  const jobs = [accountA, accountB].map((account, index) => ({
    id: `running-${index}`, account_id: account, label: `Account ${index}`, status: "running",
    course_ids: ["101", "202"], updated_at: 1700000000, created_at: 1700000000 + index,
    progress: { course_id: index ? "202" : "101", chapter_id: "2" },
  }));
  f.route = (url) => {
    if (url.endsWith("/health")) return { status: 200, body: { status: "ok" } };
    if (url.endsWith("/accounts")) return { accounts: [] };
    if (url.endsWith("/jobs")) return { jobs };
    const account = url.includes(accountB) ? accountB : accountA;
    if (url.endsWith("/courses")) return f.catalog(account);
    return cachedOutline(account, url.split("/").at(-1));
  };
  await f.run("refreshBackend()");
  await settle();
  for (const [index, job] of jobs.entries()) {
    const view = f.run(`jobViews.get("running-${index}").courses`);
    assert.equal(view.panel.hidden, false);
    assert.equal(view.entries.size, 2);
    const current = view.entries.get(job.progress.course_id);
    assert.equal(current.state.textContent, "正在学习");
    assert.equal(current.outline.current.getAttribute("aria-current"), "step");
    assert.equal(current.outline.current.children[0].textContent, `Current ${job.progress.course_id}`);
  }
  const count = f.requests.length;
  await f.run("refreshBackend()");
  assert.equal(f.requests.length, count + 3);
});

test("manual course expansion and chapter scrolling survive progress polls and course transitions", async () => {
  const f = await harness();
  f.route = (url) => cachedOutline(accountA, url.split("/").at(-1));
  await f.run(`refreshLiveCourse({ account_id: "${accountA}", progress: { course_id: "101" } })`);
  const job = {
    id: "preserved", account_id: accountA, label: "Practice", status: "running",
    course_ids: ["101", "202", "303"], updated_at: 1700000000,
    progress: { course_id: "101", chapter_id: "2" },
  };
  f.run(`remoteJobs = [${JSON.stringify(job)}]; renderJobs();`);
  const view = f.run("jobViews.get('preserved').courses");
  const current = view.entries.get("101");
  const waiting = view.entries.get("303");
  const chapter = current.outline.current;
  current.outline.list.scrollTop = 160;
  current.group.open = false;
  waiting.group.open = true;
  waiting.group.fire("toggle");
  await settle();
  assert.equal(waiting.outline.list.children.length, 2);
  f.run(`renderJob(${JSON.stringify(job)})`);
  assert.equal(current.group.open, false);
  assert.equal(waiting.group.open, true);
  assert.equal(current.outline.list.scrollTop, 160);
  assert.equal(current.outline.current, chapter);
  f.run(`renderJob(${JSON.stringify({ ...job, progress: { course_id: "202", chapter_id: "2" } })})`);
  assert.equal(view.entries.get("101"), current);
  assert.equal(view.entries.get("202").group.open, false);
  assert.equal(waiting.group.open, true);
  assert.equal(current.outline.current, null);
  assert.equal(f.requests.filter((request) => request.method && request.method !== "GET").length, 0);
});

test("missing, failed and zero-task statistics never become completed courses, even on a succeeded job", async () => {
  const f = await harness();
  f.route = () => f.catalog(accountA, [
    { id: "101", title: "Missing" },
    { id: "202", title: "Failed", summary: { ...readySummary, status: "failed", remaining_count: 0 } },
    { id: "303", title: "No tasks", summary: { ...readySummary, task_count: 0, remaining_count: 0 } },
    { id: "404", title: "Remaining", summary: readySummary },
  ]);
  await f.run(`readLiveCourseResource("${accountA}")`);
  f.run(`renderJob({ id: "uncertain", account_id: "${accountA}", status: "succeeded",
    course_ids: ["101", "202", "303", "404"], progress: {}, updated_at: 1700000000 })`);
  const view = f.run("jobViews.get('uncertain').courses");
  assert.equal(view.count.textContent, "共 4 门 · 已完成 0 门");
  assert.deepEqual([...view.entries.values()].map((entry) => entry.state.textContent),
    ["待确认", "待确认", "无任务点", "仍有剩余"]);
  assert.equal(view.entries.get("101").progress.hidden, true);
});

test("paused tasks retain their lists without claiming they are currently studying", async () => {
  const f = await harness();
  f.route = () => cachedOutline();
  await f.run(`refreshLiveCourse({ account_id: "${accountA}", progress: { course_id: "101" } })`);
  f.run(`renderJob({ id: "paused-list", account_id: "${accountA}", status: "paused",
    course_ids: ["101", "202"], progress: { course_id: "101", chapter_id: "2" }, updated_at: 1700000000 })`);
  const view = f.run("jobViews.get('paused-list').courses");
  assert.equal(view.entries.size, 2);
  assert.equal(view.entries.get("101").state.textContent, "已暂停");
  assert.equal(view.entries.get("101").outline.current.children[1].textContent, "当前章节");
});

test("all active job transitions retain their course lists including stopping", async () => {
  const f = await harness();
  f.route = () => cachedOutline();
  await f.run(`refreshLiveCourse({ account_id: "${accountA}", progress: { course_id: "101" } })`);
  const states = [
    ["queued", "排队中"], ["starting", "准备学习"], ["running", "正在学习"],
    ["retry_wait", "等待重试"], ["stopping", "停止中"], ["paused", "已暂停"],
  ];
  let group;
  let chapter;
  for (const [status, label] of states) {
    f.run(`renderJob({ id: "transition-list", account_id: "${accountA}", status: "${status}",
      course_ids: ["101", "202"], progress: { course_id: "101", chapter_id: "2" }, updated_at: 1700000000 })`);
    const view = f.run("jobViews.get('transition-list').courses");
    const entry = view.entries.get("101");
    assert.equal(view.entries.size, 2);
    assert.equal(entry.state.textContent, label);
    assert.equal(entry.outline.current.children[1].textContent, status === "running" ? "正在学习" : "当前章节");
    if (group) assert.equal(entry.group, group);
    if (chapter) assert.equal(entry.outline.current, chapter);
    group = entry.group;
    chapter = entry.outline.current;
  }
});

test("newer catalog statistics override older detailed snapshots instead of showing false completion", async () => {
  const f = await harness();
  const old = cachedOutline(accountA, "101", { updated_at: 1699999999 });
  old.detail.totals = { task_count: 1, completed_count: 1, remaining_count: 0, point_count: 1 };
  f.route = (url) => url.endsWith("/courses") ? f.catalog() : old;
  await f.run(`readLiveCourseResource("${accountA}")`);
  await f.run(`refreshLiveCourse({ account_id: "${accountA}", progress: { course_id: "101" } })`);
  f.run(`renderJob({ id: "fresh-stats", account_id: "${accountA}", status: "paused",
    course_ids: ["101"], progress: {}, updated_at: 1700000000 })`);
  const entry = f.run("jobViews.get('fresh-stats').courses.entries.get('101')");
  assert.equal(entry.state.textContent, "待学习");
  assert.match(entry.totals.textContent, /剩余 1/);
});

test("cached course reads are deduplicated and limited to four concurrent requests", async () => {
  const f = await harness();
  const releases = [];
  let concurrent = 0;
  let peak = 0;
  f.route = (url) => {
    concurrent += 1;
    peak = Math.max(peak, concurrent);
    return new Promise((resolve) => releases.push(() => {
      concurrent -= 1;
      resolve(cachedOutline(accountA, url.split("/").at(-1)));
    }));
  };
  f.run(`globalThis.pendingOutlines = Array.from({ length: 12 }, (_, i) =>
    refreshLiveCourse({ account_id: "${accountA}", progress: { course_id: String(i + 1) } }));
    refreshLiveCourse({ account_id: "${accountA}", progress: { course_id: "1" } });`);
  assert.equal(f.requests.filter((request) => request.url.includes("/courses/")).length, 4);
  for (let index = 0; index < 12; index += 1) {
    releases[index]();
    await settle();
  }
  await f.run("Promise.all(pendingOutlines)");
  assert.equal(peak, 4);
  assert.equal(f.requests.length, 13);
  assert.equal(f.requests.filter((request) => request.url.endsWith("/courses/1")).length, 1);
});

test("empty and unavailable outlines are retried at most every twenty seconds", async () => {
  const f = await harness();
  let failed = false;
  f.route = () => {
    if (failed) throw new Error("Unavailable");
    return { account_id: accountA, course_id: "101", state: "idle", detail: null };
  };
  const read = `refreshLiveCourse({ account_id: "${accountA}", progress: { course_id: "101" } })`;
  await f.run(read);
  const before = f.requests.length;
  await f.run(read);
  assert.equal(f.requests.length, before);
  f.run(`liveCourseDetails.get("${accountA}/101").attemptedAt -= 20001;`);
  failed = true;
  await f.run(read);
  assert.equal(f.requests.length, before + 1);
  assert.equal(f.run(`liveCourseDetails.get("${accountA}/101").error`), true);
  await f.run(read);
  assert.equal(f.requests.length, before + 1);
});

test("busy and failed outline reads retain the previous chapter list", async () => {
  const f = await harness();
  let phase = "done";
  f.route = () => phase === "done" ? cachedOutline()
    : phase === "busy" ? { account_id: accountA, course_id: "101", state: "running", detail: null }
      : { status: 503, body: { error: "Unavailable" } };
  const read = `refreshLiveCourse({ account_id: "${accountA}", progress: { course_id: "101" } })`;
  await f.run(read);
  f.run(`remoteJobs = [{ id: "busy-cache", account_id: "${accountA}", status: "running",
    course_ids: ["101"], progress: { course_id: "101", chapter_id: "2" }, updated_at: 1700000000 }]; renderJobs();`);
  const outline = f.run("jobViews.get('busy-cache').courses.entries.get('101').outline");
  const chapter = outline.current;
  for (phase of ["busy", "failed"]) {
    f.run(`liveCourseDetails.get("${accountA}/101").attemptedAt -= 20001;`);
    await f.run(read);
    assert.equal(outline.current, chapter);
    assert.equal(outline.list.children.length, 2);
    assert.equal(outline.status.hidden, false);
    assert.match(outline.status.textContent, /保留上次结果/);
  }
});

test("disconnecting drops queued reads and late responses cannot enter the new connection cache", async () => {
  const f = await harness();
  const releases = [];
  f.route = (url) => new Promise((resolve) => releases.push(() =>
    resolve(cachedOutline(accountA, url.split("/").at(-1)))));
  f.run(`globalThis.oldReads = Array.from({ length: 8 }, (_, i) =>
    refreshLiveCourse({ account_id: "${accountA}", progress: { course_id: String(i + 1) } }));`);
  assert.equal(releases.length, 4);
  f.run("connectionGeneration += 1; resetLiveCourse();");
  for (const release of releases) release();
  await f.run("Promise.all(oldReads)");
  assert.equal(f.run("liveCourseDetails.size"), 0);
  assert.equal(f.run("liveCourseRequestCount"), 0);
  assert.equal(f.requests.filter((request) => request.url.includes("/courses/")).length, 4);
});

test("hidden pages reduce polling and quiz states clear stale video displays", async () => {
  const f = await harness();
  f.run("remoteJobs = [{ status: 'running' }]; document.hidden = true;");
  assert.equal(f.run("refreshDelay()"), 10000);
  f.run("remoteJobs = [];");
  assert.equal(f.run("refreshDelay()"), 20000);
  f.run(`renderJob({
    id: "quiz", label: "Offline", status: "running", course_ids: ["101"], updated_at: 1700000000,
    progress: { phase: "workid", position: 20, duration: 20, ai_state: "answered", quiz_state: "submitted", quiz_answer: "false" }
  })`);
  assert.equal(f.run("jobViews.get('quiz').rows.video.row.hidden"), true);
  assert.match(f.run("jobViews.get('quiz').rows.quizState.content.textContent"), /平台已接受提交/);
  assert.equal(f.run("jobViews.get('quiz').rows.quizAnswer.content.textContent"), "false");
});

test("worker completion updates the picker and clears newly completed course selection", async () => {
  const f = await harness();
  f.route = () => f.catalog();
  await f.run("loadCourses()");
  const checkbox = f.element("course-list").querySelectorAll("input")[0];
  checkbox.checked = true;
  checkbox.fire("change");
  assert.equal(f.element("create-job").disabled, false);
  f.run(`applyLiveCourseSummary("${accountA}", {
    course: { id: "101" },
    points: [{ locked: false }],
    totals: { task_count: 1, completed_count: 1, remaining_count: 0, point_count: 1 }
  })`);
  assert.deepEqual(f.element("course-list").querySelectorAll("input").map((input) => input.value), ["202"]);
  assert.equal(f.element("create-job").disabled, true);
  assert.match(f.element("course-selection-count").textContent, /已完成 1/);
});

test("unchanged counts do not rebuild course checkboxes when JSON key order differs", async () => {
  const f = await harness();
  f.route = () => f.catalog();
  await f.run("loadCourses()");
  const checkbox = f.element("course-list").querySelectorAll("input")[0];
  checkbox.checked = true;
  checkbox.fire("change");
  f.run(`applyLiveCourseSummary("${accountA}", {
    course: { id: "101" }, points: [{ locked: false }],
    totals: { remaining_count: 1, completed_count: 0, task_count: 1, point_count: 1 }
  })`);
  assert.equal(f.element("course-list").querySelectorAll("input")[0], checkbox);
  assert.equal(checkbox.checked, true);
});

test("job filters and search preserve the mounted views and task controls", async () => {
  const f = await harness();
  f.run(`remoteJobs = [
    { id: "one", label: "Alpha", status: "running", course_ids: ["101"], updated_at: 1700000000, progress: { course: "Math" } },
    { id: "two", label: "Beta", status: "paused", course_ids: ["202"], updated_at: 1700000000, progress: {} },
    { id: "three", label: "Gamma", status: "succeeded", course_ids: ["303"], updated_at: 1700000000, progress: {} }
  ]; renderJobs();`);
  const first = f.element("jobs").children[0];
  f.element("filter-active").fire("click");
  assert.equal(f.element("jobs").children.length, 1);
  assert.equal(f.element("jobs").children[0], first);
  assert.equal(f.element("filter-active").attributes.get("aria-pressed"), "true");
  f.element("job-search").value = "missing";
  f.element("job-search").fire("input");
  assert.equal(f.element("jobs").children.length, 0);
  assert.equal(f.element("jobs-empty").hidden, false);
  assert.equal(f.element("jobs-empty").querySelector("h3").textContent, "没有匹配的任务");
  f.element("job-search").value = "MATH";
  f.element("job-search").fire("input");
  assert.equal(f.element("jobs").children[0], first);
  f.element("filter-attention").fire("click");
  assert.equal(f.element("jobs").children.length, 0);
});

test("pacing waits get their own task category instead of sitting under 执行中", async () => {
  const f = await harness();
  f.run(`remoteJobs = [
    { id: "brushing", label: "账号 A", status: "running", course_ids: ["101"], updated_at: 1700000400,
      progress: { course: "Math", phase: "video", chapter: "2.3" } },
    { id: "lunch", label: "账号 B", status: "running", course_ids: ["202"], updated_at: 1700000300,
      progress: { course: "Physics", phase: "pacing", name: "午饭时间，模拟休息，约 50 分钟", pacing_kind: "window" } },
    { id: "startjitter", label: "账号 C", status: "running", course_ids: ["303"], updated_at: 1700000200,
      progress: { course: "Chemistry", phase: "pacing", name: "开始前的准备，约 18 秒", pacing_kind: "start" } },
    { id: "stopped", label: "账号 D", status: "paused", course_ids: ["404"], updated_at: 1700000100, progress: {} }
  ]; renderJobs();`);
  const ids = () => f.element("jobs").children.map((item) => item.getAttribute("data-job-id"));

  f.element("filter-active").fire("click");
  assert.deepEqual(ids(), ["brushing"]);

  f.element("filter-pacing").fire("click");
  assert.deepEqual(ids(), ["lunch", "startjitter"]);
  assert.equal(f.element("filter-pacing").attributes.get("aria-pressed"), "true");
  assert.equal(f.element("filter-active").attributes.get("aria-pressed"), "false");

  f.element("filter-all").fire("click");
  assert.deepEqual(ids(), ["brushing", "lunch", "startjitter", "stopped"]);

  f.element("filter-pacing").fire("click");
  f.run('remoteJobs = remoteJobs.filter((job) => job.id !== "lunch" && job.id !== "startjitter"); renderJobs();');
  assert.equal(f.element("jobs").children.length, 0);
  assert.equal(f.element("jobs-empty").querySelector("h3").textContent, "现在没有任务在拟人等待");
  assert.match(f.element("jobs-empty").querySelector("p").textContent, /休息时段或随机间隔/);
});

test("task course filter shows accounts for a course and combines with status", async () => {
  const f = await harness();
  const jobs = [
    jobRecord(1, "running", {
      account_id: accountA, label: "账号 A", created_at: 100, course_ids: ["101", "202"],
      progress: { course_id: "101", course: "Advanced Math" },
    }),
    jobRecord(2, "paused", {
      account_id: accountB, label: "账号 B", created_at: 200, course_ids: ["101"],
      progress: { course_id: "202", course: "Other Course" },
    }),
    jobRecord(3, "succeeded", {
      account_id: accountA, label: "账号 A", created_at: 50, course_ids: ["202"],
      progress: { course_id: "202", course: "Other Course" },
    }),
  ];
  f.run(`
    liveCourseCatalogs.set("${accountA}", { result: { courses: [
      { id: "101", title: "Advanced Math" },
      { id: "202", title: "Other Course" }
    ] } });
    remoteJobs = ${JSON.stringify(jobs)};
    accountsFresh = true;
    renderJobs();
  `);
  const options = f.element("job-course-filter").children;
  assert.equal(options[0].value, "");
  assert.match(options.map((option) => option.textContent).join(" "), /Advanced Math \(2 个账号\)/);

  f.element("job-course-filter").value = "101";
  f.element("job-course-filter").fire("change");
  const rows = () => JSON.parse(f.run(
    'JSON.stringify(Array.from(jobsList.children).map((item) => item.getAttribute("data-job-id")))',
  ));
  assert.deepEqual(rows(), [jobs[1].id, jobs[0].id]);
  assert.equal(f.element("count").textContent, "2 / 3 个任务");

  f.element("filter-active").fire("click");
  assert.deepEqual(rows(), [jobs[0].id]);
  assert.equal(f.element("count").textContent, "1 / 3 个任务");
});

test("AI answering channel switches only to a configured API channel", async () => {
  const f = await harness();
  f.run(`updateDashboard({
    status: "ok", ai_configured: true, ai_channel: "web", ai_model: "web-model",
    ai_api_configured: false, ai_api_model: "",
  });`);
  assert.equal(f.element("ai-channel-web").attributes.get("aria-pressed"), "true");
  assert.equal(f.element("ai-channel-api").disabled, true);
  assert.match(f.element("ai-channel-status").textContent, /文本 网页 AI/);

  f.route = (url, options = {}) => {
    if (url === "/api/backend/ai/channel" && options.method === "PUT") {
      assert.equal(JSON.parse(options.body).channel, "api");
      assert.equal(JSON.parse(options.body).kind, undefined);
      return {
        channel: "api", vision_channel: "web", vision_channel_explicit: true,
        vision_enabled: true, api: { configured: true, model: "api-model", vision_model: "api-vision" },
      };
    }
    return { status: "ok", jobs: {}, accounts: [], body: {
      status: "ok", jobs: {}, visible_jobs: {}, max_workers: 30, ai_configured: true,
      ai_channel: "api", ai_api_configured: true, ai_api_model: "api-model",
      ai_api_vision_model: "api-vision", answers: {},
    } };
  };
  f.run(`updateDashboard({
    status: "ok", ai_configured: true, ai_channel: "web", ai_model: "web-model",
    ai_api_configured: true, ai_api_model: "api-model", ai_api_vision_model: "api-vision",
  });`);
  assert.equal(f.element("ai-channel-api").disabled, false);
  assert.match(f.element("ai-channel-note").textContent, /图片题自动处理已关闭/);
  f.element("ai-channel-api").fire("click");
  await settle();
  assert.ok(f.requests.some((request) => request.url === "/api/backend/ai/channel"
    && request.method === "PUT"));
  assert.match(f.element("ai-channel-status").textContent, /文本 API AI · api-model/);
  assert.match(f.element("ai-channel-note").textContent, /Chat Completions|Responses API/);
});

test("image questions take their own channel and can follow text or differ", async () => {
  const f = await harness();
  f.run(`updateDashboard({
    status: "ok", ai_configured: true, ai_channel: "api", ai_api_configured: true,
    ai_api_model: "api-model", ai_vision_channel: "api", ai_vision_channel_explicit: false,
    ai_vision_enabled: true,
  });`);
  assert.equal(f.element("ai-vision-auto").attributes.get("aria-pressed"), "true");
  assert.match(f.element("ai-channel-status").textContent, /文本 API AI · api-model｜图片 API AI/);
  assert.match(f.element("ai-channel-note").textContent, /图片题当前跟随文本题通道/);

  f.route = (url, options = {}) => {
    if (url === "/api/backend/ai/channel" && options.method === "PUT") {
      const body = JSON.parse(options.body);
      assert.deepEqual(body, { channel: "web", kind: "vision" });
      return {
        channel: "api", vision_channel: "web", vision_channel_explicit: true,
        vision_enabled: true, api: { configured: true, model: "api-model", protocol: "responses" },
      };
    }
    return { ok: true };
  };
  f.element("ai-vision-web").fire("click");
  await settle();
  assert.equal(f.element("ai-vision-web").attributes.get("aria-pressed"), "true");
  assert.equal(f.element("ai-vision-auto").attributes.get("aria-pressed"), "false");
  assert.equal(f.element("ai-channel-api").attributes.get("aria-pressed"), "true");
  assert.match(f.element("ai-channel-status").textContent, /文本 API AI · api-model｜图片 网页 AI/);
  assert.match(f.element("ai-channel-note").textContent, /图片题已单独指定通道/);

  f.route = (url, options = {}) => {
    if (url === "/api/backend/ai/channel" && options.method === "PUT") {
      assert.deepEqual(JSON.parse(options.body), { channel: "auto", kind: "vision" });
      return {
        channel: "api", vision_channel: "api", vision_channel_explicit: false,
        vision_enabled: true, api: { configured: true, model: "api-model" },
      };
    }
    return { ok: true };
  };
  f.element("ai-vision-auto").fire("click");
  await settle();
  assert.equal(f.element("ai-vision-auto").attributes.get("aria-pressed"), "true");
  assert.match(f.element("ai-channel-note").textContent, /图片题当前跟随文本题通道/);
});

test("the image question switch posts the global toggle", async () => {
  const f = await harness();
  f.run(`updateDashboard({
    status: "ok", ai_configured: true, ai_channel: "api", ai_api_configured: true,
    ai_api_model: "api-model", ai_vision_channel: "web", ai_vision_channel_explicit: true,
    ai_vision_enabled: false,
  });`);
  assert.equal(f.element("ai-vision-enabled").checked, false);
  f.route = (url, options = {}) => {
    if (url === "/api/backend/ai/vision" && options.method === "PUT") {
      assert.deepEqual(JSON.parse(options.body), { enabled: true });
      return {
        channel: "api", vision_channel: "web", vision_channel_explicit: true,
        vision_enabled: true, api: { configured: true, model: "api-model" },
      };
    }
    return { ok: true };
  };
  f.element("ai-vision-enabled").checked = true;
  f.element("ai-vision-enabled").fire("change");
  await settle();
  assert.ok(f.requests.some((request) => request.url === "/api/backend/ai/vision"
    && request.method === "PUT"));
  assert.equal(f.element("ai-vision-enabled").checked, true);
  assert.match(f.element("ai-channel-status").textContent, /图片 网页 AI/);
});

test("the image fallback switch posts and explains the retry chain", async () => {
  const f = await harness();
  f.run(`updateDashboard({
    status: "ok", ai_configured: true, ai_channel: "api", ai_api_configured: true,
    ai_api_model: "api-model", ai_vision_channel: "api", ai_vision_channel_explicit: true,
    ai_vision_enabled: true, ai_vision_fallback: true, ai_vision_api_separate: true,
    ai_vision_api_model: "vision-model",
  });`);
  assert.equal(f.element("ai-vision-fallback").checked, true);
  assert.match(f.element("ai-channel-status").textContent, /图片 API AI · vision-model/);
  assert.match(f.element("ai-channel-note").textContent, /自动退回网页 AI/);

  f.route = (url, options = {}) => {
    if (url === "/api/backend/ai/vision-fallback" && options.method === "PUT") {
      assert.deepEqual(JSON.parse(options.body), { enabled: false });
      return {
        channel: "api", vision_channel: "api", vision_channel_explicit: true,
        vision_enabled: true, vision_fallback: false, vision_chain: ["api"],
        vision_api: { separate: true, model: "vision-model", configured: true },
        api: { configured: true, model: "api-model" },
      };
    }
    return { ok: true };
  };
  f.element("ai-vision-fallback").checked = false;
  f.element("ai-vision-fallback").fire("change");
  await settle();
  assert.equal(f.element("ai-vision-fallback").checked, false);
  assert.match(f.element("ai-channel-note").textContent, /不会改用网页 AI/);
});

test("the one-shot image switch posts and rewrites the channel note", async () => {
  const f = await harness();
  f.run(`updateDashboard({
    status: "ok", ai_configured: true, ai_channel: "api", ai_api_configured: true,
    ai_api_model: "api-model", ai_vision_channel: "api", ai_vision_channel_explicit: true,
    ai_vision_enabled: true, ai_vision_fallback: true, ai_vision_direct: true,
    ai_api_protocol: "responses",
  });`);
  assert.equal(f.element("ai-vision-direct").checked, true);
  assert.match(f.element("ai-channel-note").textContent, /一次直答/);
  assert.match(f.element("ai-channel-note").textContent, /Responses API/);

  f.route = (url, options = {}) => {
    if (url === "/api/backend/ai/vision-direct" && options.method === "PUT") {
      assert.deepEqual(JSON.parse(options.body), { enabled: false });
      return {
        channel: "api", vision_channel: "api", vision_channel_explicit: true,
        vision_enabled: true, vision_fallback: true, vision_direct: false,
        vision_chain: ["api", "web"],
        api: { configured: true, model: "api-model" },
      };
    }
    return { ok: true };
  };
  f.element("ai-vision-direct").checked = false;
  f.element("ai-vision-direct").fire("change");
  await settle();
  assert.equal(f.element("ai-vision-direct").checked, false);
  assert.match(f.element("ai-channel-note").textContent, /识别→解题→复核三步/);
});

test("task cards keep a fixed creation-time order as progress updates", async () => {
  const f = await harness();
  const older = jobRecord(1, "running", { created_at: 100, updated_at: 500 });
  const newer = jobRecord(2, "paused", { created_at: 200, updated_at: 300 });
  f.run(`remoteJobs = ${JSON.stringify([older, newer])}; renderJobs(); renderOverviewJobs();`);
  const order = () => Array.from(f.run(
    'Array.from(jobsList.children).map((item) => item.getAttribute("data-job-id"))',
  ));
  const overviewOrder = () => Array.from(f.run(
    'Array.from(overviewJobsList.children).map((item) => item.getAttribute("data-job-id"))',
  ));
  assert.deepEqual(order(), [newer.id, older.id]);
  assert.deepEqual(overviewOrder(), [newer.id, older.id]);

  f.run(`remoteJobs[1].updated_at = 999999; renderJobs(); renderOverviewJobs();`);
  assert.deepEqual(order(), [newer.id, older.id]);
  assert.deepEqual(overviewOrder(), [newer.id, older.id]);
});

test("archived jobs use a separate page and can be restored without restarting", async () => {
  const f = await harness();
  const job = jobRecord(4, "succeeded");
  let visible = job;
  let archived = null;
  f.route = (url, options = {}) => {
    if (url === "/api/backend/jobs?archived=1") return { jobs: archived ? [archived] : [] };
    if (url === `/api/backend/jobs/${job.id}/archive` && options.method === "POST") {
      archived = { ...visible, archived_at: 1800000000 };
      visible = null;
      return archived;
    }
    if (url === `/api/backend/jobs/${job.id}/unarchive` && options.method === "POST") {
      visible = { ...archived, archived_at: null };
      archived = null;
      return visible;
    }
    if (url === "/api/backend/jobs") return { jobs: visible ? [visible] : [] };
    if (url.endsWith("/health")) return { status: "ok", jobs: {}, visible_jobs: {}, max_workers: 30 };
    if (url.endsWith("/accounts")) return { accounts: [accountRecord()] };
    return { jobs: [] };
  };

  f.run(`remoteJobs = [${JSON.stringify(job)}]; accountsFresh = true; renderJobs();`);
  f.run(`jobViews.get("${job.id}").actions.children.find((button) => button.getAttribute("data-action") === "archive").fire("click")`);
  await settle();
  assert.equal(f.run("remoteJobs.length"), 0);
  assert.equal(f.run("archivedJobs.length"), 1);
  assert.equal(f.element("jobs").children.length, 0);

  f.run('selectView("archived")');
  await f.run("refreshArchivedJobs()");
  assert.equal(f.element("archived-jobs").children.length, 1);
  assert.equal(f.requests.filter((request) => request.url.includes("/jobs?archived=1")).length, 1);
  await f.run("refreshArchivedJobs()");
  assert.equal(f.requests.filter((request) => request.url.includes("/jobs?archived=1")).length, 1);
  await f.run("refreshArchivedJobs(true)");
  assert.equal(f.requests.filter((request) => request.url.includes("/jobs?archived=1")).length, 2);
  const archivedView = f.run(`archivedJobViews.get("${job.id}")`);
  assert.equal(archivedView.actions.children[0].getAttribute("data-action"), "unarchive");
  f.run(`archivedJobViews.get("${job.id}").actions.children[0].fire("click")`);
  await settle();
  assert.equal(f.run("archivedJobs.length"), 0);
  assert.equal(f.run("remoteJobs.length"), 1);
  assert.equal(f.run("remoteJobs[0].status"), "succeeded");
  assert.equal(f.run("remoteJobs[0].archived_at"), null);
  assert.equal(f.element("archived-jobs").children.length, 0);
  assert.equal(f.requests.filter((request) => request.url.endsWith("/jobs") && request.method === "POST").length, 0);
  const html = fs.readFileSync(new URL("../internal/server/static/index.html", import.meta.url), "utf8");
  assert.match(html, /<option value="archive">归档已结束任务<\/option>/);
});

test("archived jobs are grouped by account and search keeps matching groups", async () => {
  const f = await harness();
  const jobs = [
    jobRecord(1, "succeeded", {
      account_id: accountB, label: "账号 B", username_masked: "b***02",
      created_at: 100, archived_at: 1800000001,
    }),
    jobRecord(2, "failed", {
      account_id: accountA, label: "账号 A", username_masked: "a***01",
      created_at: 300, archived_at: 1800000002,
    }),
    jobRecord(3, "cancelled", {
      account_id: accountA, label: "账号 A", username_masked: "a***01",
      created_at: 200, archived_at: 1800000003,
    }),
  ];
  f.run(`archivedJobs = ${JSON.stringify(jobs)}; renderArchivedJobs();`);
  const groups = () => JSON.parse(f.run(`JSON.stringify(
    Array.from(archivedJobsList.children).map((group) => ({
      accountID: group.getAttribute("data-account-id"),
      title: group.children[0].children[0].children[0].textContent,
      count: group.children[0].children[1].textContent,
      jobIDs: Array.from(group.children[1].children).map((item) => item.getAttribute("data-job-id")),
    }))
  )`));
  assert.deepEqual(groups(), [
    { accountID: accountA, title: "账号 A", count: "2 个归档任务", jobIDs: [jobs[1].id, jobs[2].id] },
    { accountID: accountB, title: "账号 B", count: "1 个归档任务", jobIDs: [jobs[0].id] },
  ]);
  assert.match(f.element("archived-summary").textContent, /3 条归档记录 · 2 个账号/);

  f.element("archived-search").value = "Course 1";
  f.element("archived-search").fire("input");
  assert.deepEqual(groups(), [
    { accountID: accountB, title: "账号 B", count: "1 个归档任务", jobIDs: [jobs[0].id] },
  ]);
});

test("archived job cards are not retained in the main task view cache", async () => {
  const f = await harness();
  const archived = jobRecord(5, "failed", { archived_at: 1800000000 });
  f.run(`remoteJobs = []; archivedJobs = [${JSON.stringify(archived)}]; renderJobs(); renderArchivedJobs();`);
  assert.equal(f.element("jobs").children.length, 0);
  assert.equal(f.element("archived-jobs").children.length, 1);
  assert.equal(f.run(`jobViews.has("${archived.id}")`), false);
  assert.equal(f.run(`archivedJobViews.has("${archived.id}")`), true);
  f.run("resetArchivedJobs();");
  assert.equal(f.run("archivedJobs.length"), 0);
  assert.equal(f.element("archived-jobs").children.length, 0);
});

test("overview summarizes running and attention jobs and opens the task view", async () => {
  const f = await harness();
  f.run(`remoteJobs = [
    { id: "active", label: "Offline", status: "running", course_ids: ["101"], updated_at: 1700000000,
      progress: { course: "军事技能训练", chapter: "心肺复苏", phase: "video", name: "CPR.mp4",
        position: 60, duration: 120, completed: 2, total: 8 } },
    { id: "attention", label: "Offline", status: "paused", course_ids: ["202"], updated_at: 1700000001,
      progress: { course: "心理健康教育", chapter: "测验", phase: "workid", ai_state: "queued" } },
    { id: "done", label: "Finished", status: "succeeded", course_ids: ["303"], updated_at: 1700000002, progress: {} },
  ]; renderOverviewJobs();`);
  const text = (node) => [node.textContent, ...(node.children || []).flatMap(text)].join(" ");
  assert.equal(f.element("overview-jobs").children.length, 2);
  assert.match(text(f.element("overview-jobs")), /军事技能训练/);
  assert.match(text(f.element("overview-jobs")), /01:00 \/ 02:00/);
  assert.match(text(f.element("overview-jobs")), /等待 AI 回复/);
  assert.match(f.element("overview-jobs-summary").textContent,
    /1 个运行或排队（0 个拟人等待） · 1 个待处理/);
  assert.equal(f.element("overview-jobs-empty").hidden, true);
  f.element("tasks-tab").fire("click");
  assert.equal(f.element("overview-view").hidden, true);
  assert.equal(f.element("tasks-view").hidden, false);
  f.element("overview-tab").fire("click");
  f.element("overview-open-tasks").fire("click");
  assert.equal(f.element("overview-view").hidden, true);
  assert.equal(f.element("tasks-view").hidden, false);
});

test("a rolled-back video is marked red with the platform drop count", async () => {
  const f = await harness();
  f.run(`remoteJobs = [
    { id: "rolled", label: "Offline", status: "running", course_ids: ["101"], updated_at: 1700000000,
      progress: { course: "军事理论", chapter: "1.2 中国国防的历史", phase: "video",
        name: "1.2.mp4", position: 120, duration: 900, video_state: "rolled_back",
        rollback_from: 620, rollback_to: 120, rollback_dropped: 500, rollback_count: 2 } },
    { id: "clean", label: "Offline", status: "running", course_ids: ["202"], updated_at: 1700000001,
      progress: { course: "心理健康教育", phase: "video", name: "2.1.mp4", position: 30, duration: 600 } },
  ]; renderOverviewJobs();`);
  const items = f.element("overview-jobs").children;
  assert.equal(items.length, 2);
  assert.equal(items[0].classList.contains("job-rolled-back"), true);
  assert.equal(items[1].classList.contains("job-rolled-back"), false);
  const text = (node) => [node.textContent, ...(node.children || []).flatMap(text)].join(" ");
  assert.match(text(items[0]), /平台回退了进度|平台已回退 2 次/);
  assert.doesNotMatch(text(items[1]), /回退/);
});

test("overview distinguishes backend health from configured and live AI workers", async () => {
  const f = await harness();
  f.run(`updateDashboard({
    status: "ok", ai_configured: true, ai_workers: 3, ai_workers_alive: 2,
    jobs: { running: 4, queued: 1, paused: 1, failed: 2 },
    answers: { running: 1, queued: 2 },
  });`);
  assert.equal(f.element("overview-backend-state").textContent, "在线");
  assert.equal(f.element("overview-backend-state").dataset.state, "online");
  assert.equal(f.element("overview-ai-state").textContent, "部分在线");
  assert.equal(f.element("overview-ai-state").dataset.state, "degraded");
  assert.match(f.element("overview-ai-note").textContent, /2 \/ 3 个 AI 工作线程在线/);
  assert.equal(f.element("metric-pending").textContent, "3");
  f.run(`updateDashboard({ status: "ok", ai_configured: true, ai_workers: 2, ai_workers_alive: 0 });`);
  assert.equal(f.element("overview-ai-state").textContent, "工作线程离线");
  assert.equal(f.element("overview-ai-state").dataset.state, "offline");
  f.run("updateOverviewUnavailable()");
  assert.equal(f.element("overview-ai-state").dataset.state, "unknown");
  f.run("updateDashboard({});");
  assert.equal(f.element("overview-ai-state").textContent, "待确认");
});

test("dashboard metrics use server counts and never expose storage secrets", async () => {
  const f = await harness();
  f.run(`updateDashboard({
    status: "ok", max_workers: 30, ai_configured: true, ai_workers: 2, ai_workers_alive: 2,
    jobs: { running: 3, queued: 2, starting: 1, failed: 1, succeeded: 4 },
    answers: { running: 1, queued: 2 },
    storage: { engine: "sqlite", database_bytes: 1048576 }
  });`);
  assert.equal(f.element("metric-running").textContent, "3");
  assert.equal(f.element("metric-queued").textContent, "3");
  assert.equal(f.element("metric-ai").textContent, "3");
  assert.equal(f.element("metric-pending").textContent, "1");
  assert.match(f.element("database-status").textContent, /SQLite · WAL · 1.0 MB/);
  assert.match(f.element("metric-attention").textContent, /1 个异常 · 0 个暂停/);
});

test("shared question-bank states and persistent reuse metrics are displayed", async () => {
  const f = await harness();
  f.run(`updateDashboard({
    ai_configured: false, answers: { running: 0, queued: 0 },
    question_bank: { entries: 120, hits: 90, shared_waits: 29 }
  });`);
  assert.match(f.element("metric-ai-status").textContent, /题库 120 · 命中 90/);
  assert.equal(f.element("metric-ai-status").title, "共享等待 29 次");
  assert.equal(f.run("aiStateLabels.shared_wait"), "等待同题共享结果");
  assert.equal(f.run("aiStateLabels.shared"), "已复用同题结果");
  f.run("updateDashboard({});");
  assert.equal(f.element("metric-ai-status").title, "");
  assert.doesNotMatch(f.element("metric-ai-status").textContent, /120|90/);
});

function jobRecord(index, status = "paused", overrides = {}) {
  return {
    id: String(index).repeat(32), account_id: accountA, label: `Task ${index}`,
    status, course_ids: [String(index)], updated_at: 1700000000,
    progress: { course: `Course ${index}` }, ...overrides,
  };
}

function showJobs(f, jobs) {
  f.jobs = jobs;
  f.route = (url, options) => {
    if (url.endsWith("/jobs/batch")) return f.batch(JSON.parse(options.body));
    if (url.endsWith("/jobs")) return { jobs: f.jobs };
    if (url.endsWith("/accounts")) return { accounts: [] };
    if (url.endsWith("/health")) return { body: { status: "ok", max_workers: 30, jobs: {} } };
    return { account_id: accountA, state: "idle", courses: [] };
  };
  f.run(`remoteJobs = ${JSON.stringify(jobs)}; accountsFresh = true; renderJobs();`);
}

function chooseJob(f, id) {
  const checkbox = f.run(`jobViews.get("${id}").selection`);
  checkbox.checked = true;
  checkbox.fire("change");
}

test("task selection filters only the current list and opening batch never starts a task", async () => {
  const f = await harness();
  const paused = jobRecord(1);
  const running = jobRecord(2, "running");
  showJobs(f, [paused, running]);
  f.element("filter-active").fire("click");
  f.element("select-visible-jobs").checked = true;
  f.element("select-visible-jobs").fire("change");
  assert.equal(f.run("selectedJobIDs.size"), 1);
  assert.equal(f.run(`selectedJobIDs.has("${running.id}")`), true);
  f.element("filter-all").fire("click");
  assert.equal(f.element("select-visible-jobs").indeterminate, true);
  f.element("quick-pause-jobs").fire("click");
  assert.equal(f.element("job-batch-dialog").open, true);
  assert.equal(f.element("job-batch-action").value, "pause");
  assert.match(f.element("job-batch-submit-label").textContent, /暂停 1 项/);
  assert.equal(f.requests.filter((request) => request.method === "POST").length, 0);
});

test("batch search, eligible select-all and card checkboxes share one persistent selection", async () => {
  const f = await harness();
  const jobs = [jobRecord(1), jobRecord(2), jobRecord(3, "running")];
  showJobs(f, jobs);
  chooseJob(f, jobs[0].id);
  f.element("open-job-batch").fire("click");
  f.element("job-batch-search").value = "Course 2";
  f.element("job-batch-search").fire("input");
  f.element("job-batch-select-all").checked = true;
  f.element("job-batch-select-all").fire("change");
  assert.equal(f.run("selectedJobIDs.size"), 2);
  assert.equal(f.element("job-batch-list").children.length, 1);
  assert.equal(f.run(`jobViews.get("${jobs[1].id}").selection.checked`), true);
  f.element("job-batch-search").value = "";
  f.element("job-batch-search").fire("input");
  assert.equal(f.run(`jobBatchViews.get("${jobs[2].id}").checkbox.disabled`), true);
  f.element("job-batch-clear").fire("click");
  assert.equal(f.run("selectedJobIDs.size"), 0);
  assert.equal(f.element("job-batch-submit").disabled, true);
});

test("every batch action enables only its corresponding states and never silently retries finished jobs", async () => {
  const f = await harness();
  const states = ["paused", "running", "queued", "stopping", "failed", "cancelled", "succeeded", "needs_attention"];
  const jobs = states.map((state, index) => jobRecord(index + 1, state));
  showJobs(f, jobs);
  f.element("select-visible-jobs").checked = true;
  f.element("select-visible-jobs").fire("change");
  f.element("open-job-batch").fire("click");
  const expected = {
    resume: ["paused"], pause: ["running", "queued"],
    retry: ["failed", "cancelled", "succeeded", "needs_attention"],
    cancel: ["paused", "running", "queued", "needs_attention"],
    archive: ["failed", "cancelled", "succeeded"],
    delete: ["failed", "cancelled", "succeeded", "needs_attention"],
  };
  for (const [action, eligible] of Object.entries(expected)) {
    f.element("job-batch-action").value = action;
    f.element("job-batch-action").fire("change");
    const enabled = jobs.filter((job) => !f.run(`jobBatchViews.get("${job.id}").checkbox.disabled`))
      .map((job) => job.status);
    assert.deepEqual(enabled, eligible);
    assert.equal(f.element("job-batch-confirm-label").hidden, !["cancel", "delete"].includes(action));
  }
  assert.equal(f.requests.filter((request) => request.method === "POST").length, 0);
});

test("batch archive moves selected finished jobs into the archive list", async () => {
  const f = await harness();
  const job = jobRecord(6, "failed");
  showJobs(f, [job]);
  chooseJob(f, job.id);
  f.element("open-job-batch").fire("click");
  f.element("job-batch-action").value = "archive";
  f.element("job-batch-action").fire("change");
  f.batch = ({ action, job_ids }) => {
    f.jobs = [];
    const archived = { ...job, archived_at: 1800000000 };
    return {
      action, requested: job_ids.length, succeeded: job_ids.length, failed: 0,
      results: job_ids.map((id) => ({ id, ok: true, job: archived })),
    };
  };
  f.element("job-batch-form").fire("submit");
  await settle();
  assert.equal(f.run("remoteJobs.length"), 0);
  assert.equal(f.run("archivedJobs.length"), 1);
  assert.equal(f.element("jobs").children.length, 0);
  assert.equal(f.element("job-batch-submit").disabled, true);
  assert.match(f.element("job-batch-result").textContent, /归档完成：成功 1 项/);
});

test("batch submissions send explicit eligible IDs once, retaining individual failures for retry", async () => {
  const f = await harness();
  const jobs = [jobRecord(1), jobRecord(2), jobRecord(3, "running")];
  showJobs(f, jobs);
  for (const job of jobs) chooseJob(f, job.id);
  f.element("quick-resume-jobs").fire("click");
  let release;
  f.batch = () => new Promise((resolve) => { release = resolve; });
  f.element("job-batch-form").fire("submit");
  f.element("job-batch-form").fire("submit");
  assert.equal(f.element("job-batch-form").attributes.get("aria-busy"), "true");
  assert.equal(f.element("job-batch-close").disabled, true);
  f.element("job-batch-close").fire("click");
  assert.equal(f.element("job-batch-dialog").open, true);
  assert.equal(f.element("job-batch-dialog").fire("cancel").defaultPrevented, true);
  f.run(`jobViews.get("${jobs[2].id}").actions.children[0].fire("click")`);
  const requests = f.requests.filter((request) => request.method === "POST");
  assert.equal(requests.length, 1);
  assert.deepEqual(JSON.parse(requests[0].body), {
    action: "resume", job_ids: jobs.slice(0, 2).map((job) => job.id),
  });
  f.jobs = [{ ...jobs[0], status: "queued" }, jobs[1], jobs[2]];
  release({ action: "resume", results: [
    { id: jobs[0].id, ok: true, job: f.jobs[0] },
    { id: jobs[1].id, ok: false, code: "STATE_CONFLICT", error: "Another job is already active for this account" },
  ] });
  await settle();
  assert.equal(f.element("job-batch-dialog").open, true);
  assert.match(f.element("job-batch-result").textContent, /成功 1 项 · 失败 1 项/);
  assert.equal(f.run(`selectedJobIDs.has("${jobs[0].id}")`), false);
  assert.equal(f.run(`selectedJobIDs.has("${jobs[1].id}")`), true);
  assert.match(f.run(`jobBatchViews.get("${jobs[1].id}").result.textContent`), /已有活动或暂停任务/);
  assert.equal(f.element("job-batch-submit-label").textContent, "开启 1 项");
  assert.equal(f.element("job-batch-close").disabled, false);
});

test("cancellation and deletion require explicit confirmation which resets when targets change", async () => {
  const f = await harness();
  const jobs = [jobRecord(1, "cancelled"), jobRecord(2, "failed")];
  showJobs(f, jobs);
  chooseJob(f, jobs[0].id);
  f.element("open-job-batch").fire("click");
  f.element("job-batch-action").value = "delete";
  f.element("job-batch-action").fire("change");
  f.element("job-batch-form").fire("submit");
  assert.equal(f.requests.filter((request) => request.method === "POST").length, 0);
  f.element("job-batch-confirm").checked = true;
  f.element("job-batch-confirm").fire("change");
  assert.equal(f.element("job-batch-submit").disabled, false);
  const second = f.run(`jobBatchViews.get("${jobs[1].id}").checkbox`);
  second.checked = true;
  second.fire("change");
  assert.equal(f.element("job-batch-confirm").checked, false);
  f.element("job-batch-confirm").checked = true;
  f.element("job-batch-confirm").fire("change");
  f.batch = ({ job_ids }) => {
    f.jobs = [];
    return { action: "delete", results: job_ids.map((id) => ({ id, ok: true, deleted: true })) };
  };
  f.element("job-batch-form").fire("submit");
  await settle();
  assert.equal(f.element("jobs").children.length, 0);
  assert.equal(f.run("selectedJobIDs.size"), 0);
  assert.match(f.element("job-batch-result").textContent, /成功 2 项/);
  assert.equal(f.element("job-batch-submit").disabled, true);
});

test("batch polling preserves mounted rows, card selections, search text and scroll", async () => {
  const f = await harness();
  const job = jobRecord(1);
  showJobs(f, [job]);
  chooseJob(f, job.id);
  f.element("open-job-batch").fire("click");
  f.element("job-batch-search").value = "Course";
  f.element("job-batch-search").fire("input");
  f.element("job-batch-list").scrollTop = 80;
  const row = f.element("job-batch-list").children[0];
  const checkbox = f.run(`jobViews.get("${job.id}").selection`);
  f.jobs = [{ ...job, updated_at: job.updated_at + 1 }];
  await f.run("refreshBackend()");
  assert.equal(f.element("job-batch-list").children[0], row);
  assert.equal(f.run(`jobViews.get("${job.id}").selection`), checkbox);
  assert.equal(checkbox.checked, true);
  assert.equal(f.element("job-batch-search").value, "Course");
  assert.equal(f.element("job-batch-list").scrollTop, 80);
});

test("lost connections retain batch selection but disable mutations until refreshed", async () => {
  const f = await harness();
  const job = jobRecord(1);
  showJobs(f, [job]);
  chooseJob(f, job.id);
  f.element("open-job-batch").fire("click");
  const online = f.route;
  f.route = () => { throw new Error("Offline"); };
  await f.run("refreshBackend()");
  assert.equal(f.element("job-batch-submit").disabled, true);
  assert.equal(f.run("selectedJobIDs.size"), 1);
  assert.equal(f.element("job-batch-dialog").open, true);
  f.element("job-batch-form").fire("submit");
  assert.equal(f.requests.filter((request) => request.method === "POST").length, 0);
  f.route = online;
  await f.run("refreshBackend()");
  assert.equal(f.element("job-batch-submit").disabled, false);
});

test("ambiguous batch failures refresh status without automatically repeating the request", async () => {
  const f = await harness();
  const job = jobRecord(1);
  showJobs(f, [job]);
  chooseJob(f, job.id);
  f.element("open-job-batch").fire("click");
  f.batch = () => { throw new Error("Network interrupted"); };
  f.element("job-batch-form").fire("submit");
  await settle();
  assert.match(f.element("job-batch-error").textContent, /部分操作可能已生效/);
  assert.equal(f.run("selectedJobIDs.size"), 1);
  assert.equal(f.requests.filter((request) => request.url.endsWith("/jobs/batch")).length, 1);
  assert.ok(f.requests.some((request) => request.url.endsWith("/jobs") && !request.method));
});

test("incomplete batch responses are not presented as success", async () => {
  const f = await harness();
  const job = jobRecord(1);
  showJobs(f, [job]);
  chooseJob(f, job.id);
  f.element("open-job-batch").fire("click");
  f.batch = () => ({ action: "resume", results: [] });
  f.element("job-batch-form").fire("submit");
  await settle();
  assert.match(f.element("job-batch-error").textContent, /批量响应不完整/);
  assert.equal(f.element("job-batch-result").hidden, true);
  assert.equal(f.run("selectedJobIDs.size"), 1);
});

test("late polling cannot restore jobs removed by a successful batch delete", async () => {
  const f = await harness();
  const job = jobRecord(1, "cancelled");
  showJobs(f, [job]);
  chooseJob(f, job.id);
  f.element("open-job-batch").fire("click");
  f.element("job-batch-action").value = "delete";
  f.element("job-batch-action").fire("change");
  f.element("job-batch-confirm").checked = true;
  f.element("job-batch-confirm").fire("change");
  const online = f.route;
  let release;
  f.route = (url, options) => url.endsWith("/jobs") && !options.method
    ? new Promise((resolve) => { release = resolve; }) : online(url, options);
  const polling = f.run("refreshBackend()");
  f.batch = () => ({ action: "delete", results: [{ id: job.id, ok: true, deleted: true }] });
  f.element("job-batch-form").fire("submit");
  await settle();
  assert.equal(f.element("jobs").children.length, 0);
  release({ jobs: [job] });
  await polling;
  assert.equal(f.element("jobs").children.length, 0);
  assert.ok([...f.timers.values()].some((timer) => timer.delay === 0));
});

test("explicit disconnection closes the batch dialog and late responses cannot affect a new connection", async () => {
  const f = await harness();
  const job = jobRecord(1);
  showJobs(f, [job]);
  chooseJob(f, job.id);
  f.element("open-job-batch").fire("click");
  let release;
  f.batch = () => new Promise((resolve) => { release = resolve; });
  f.element("job-batch-form").fire("submit");
  f.element("disconnect-backend").fire("click");
  await settle();
  assert.equal(f.element("job-batch-dialog").open, false);
  assert.equal(f.run("selectedJobIDs.size"), 0);
  showJobs(f, [jobRecord(2)]);
  release({ action: "resume", results: [{ id: job.id, ok: true, job: { ...job, status: "queued" } }] });
  await settle();
  assert.equal(f.run("remoteJobs.length"), 1);
  assert.equal(f.run("remoteJobs[0].id"), jobRecord(2).id);
  assert.equal(f.element("job-batch-result").hidden, true);
});

test("pacing pauses explain why a running job is idle", async () => {
  const f = await harness();
  assert.equal(
    f.run(`accountActivity({status: "running", progress: {phase: "pacing", name: "午饭时间，模拟休息，约 50 分钟"}})`),
    "拟人节奏等待中 · 午饭时间，模拟休息，约 50 分钟",
  );
  assert.equal(f.run(`phaseLabels.pacing`), "拟人节奏等待中");
  const html = fs.readFileSync(new URL("../internal/server/static/index.html", import.meta.url), "utf8");
  assert.match(html, /id="human-pacing"[^>]*checked>拟人节奏/);
});
