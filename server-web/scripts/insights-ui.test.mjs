import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = fs.readFileSync(new URL("../internal/server/static/insights.js", import.meta.url), "utf8");
const markup = fs.readFileSync(new URL("../internal/server/static/index.html", import.meta.url), "utf8");
const questionID = "a".repeat(32);

class Element {
  constructor(tag = "div") {
    this.tag = tag;
    this.children = [];
    this.listeners = new Map();
    this.attributes = new Map();
    this.style = { setProperty: (name, value) => this.attributes.set(`style:${name}`, value) };
    this.parentElement = null;
    this.value = "";
    this.textContent = "";
    this.hidden = false;
    this.disabled = false;
    this.open = false;
  }
  addEventListener(name, handler) {
    const handlers = this.listeners.get(name) || [];
    handlers.push(handler);
    this.listeners.set(name, handlers);
  }
  fire(name) {
    for (const handler of this.listeners.get(name) || []) {
      handler({ target: this, currentTarget: this, preventDefault() {} });
    }
  }
  setAttribute(name, value) { this.attributes.set(name, value); }
  removeAttribute(name) { this.attributes.delete(name); }
  get lastElementChild() { return this.children.at(-1) || null; }
  scrollIntoView() { this.scrolled = true; }
  append(...children) { for (const child of children) this.insertBefore(child, null); }
  insertBefore(child, before) {
    child.remove();
    const index = before ? this.children.indexOf(before) : this.children.length;
    assert.ok(index >= 0);
    this.children.splice(index, 0, child);
    child.parentElement = this;
  }
  remove() {
    if (!this.parentElement) return;
    const siblings = this.parentElement.children;
    siblings.splice(siblings.indexOf(this), 1);
    this.parentElement = null;
  }
  replaceChildren(...children) {
    for (const child of [...this.children]) child.remove();
    this.append(...children);
  }
  showModal() { this.open = true; }
  close() { this.open = false; this.fire("close"); }
}

async function settle() {
  for (let index = 0; index < 6; index++) await new Promise(setImmediate);
}

function harness({ images = false } = {}) {
  const elements = new Map([...markup.matchAll(/\bid="([^"]+)"/g)].map((match) => [match[1], new Element()]));
  const element = (id) => {
    assert.ok(elements.has(id), `Missing element ${id}`);
    return elements.get(id);
  };
  element("bank-page-size").value = "20";
  element("bank-sort").value = "updated";
  const events = new Element();
  const timers = new Map();
  const requests = [];
  const fixture = {
    element, timers, requests, events, now: 1700000000000,
    question: {
      id: questionID, course_id: "101", course_title: "Offline course",
      title: "1 + 1 = ?", type: "single", answer_preview: "B. 2", hits: 12,
      shared_waits: 3, content_bytes: 150, created_at: 100, updated_at: 200,
      options: "A. 1\nB. 2", answers: ["B. 2"],
    },
    resources: {
      status: "ok", platform: "linux", sampled_at: 1700000000,
      cpu: { percent: 25.5, logical_count: 6, load_average: [0.5, 0.4, 0.3] },
      memory: { percent: 50, used_bytes: 1073741824, total_bytes: 2147483648 },
      disk: { percent: 25, used_bytes: 1073741824, total_bytes: 4294967296, free_bytes: 3221225472 },
      disks: [
        { role: "system", percent: 25, used_bytes: 1073741824, total_bytes: 4294967296,
          free_bytes: 3221225472, device: "/dev/vda1", mountpoint: "/" },
        { role: "data", percent: 10, used_bytes: 1073741824, total_bytes: 10737418240,
          free_bytes: 9663676416, device: "/dev/vdb1", mountpoint: "/data" },
      ],
      data_disk_shared: false, data_directory: "/data/chaoxing/backend",
      network: { received_bytes: 1048576, sent_bytes: 2048, received_bytes_per_second: 1024, sent_bytes_per_second: 512 },
      swap: { enabled: false }, uptime_seconds: 90061, backend_rss_bytes: 10485760,
    },
    stats: {
      entries: 1, hits: 12, shared_waits: 3, course_count: 1, sampled_at: 1700000000,
      courses: [{ id: "101", title: "Offline course", entries: 1 }],
      storage: { content_bytes: 150, database_bytes: 1048576, question_bytes: 120, answer_bytes: 30 },
    },
  };
  fixture.route = (path) => {
    if (path === "/resources") return fixture.resources;
    if (path === "/question-bank/stats") return fixture.stats;
    if (path.startsWith("/question-bank?")) return { items: [fixture.question], total: 1, pages: 1, page: 1, page_size: 20 };
    if (path === `/question-bank/${questionID}`) return fixture.question;
    throw new Error(`Unknown fixture route ${path}`);
  };
  let timerID = 0;
  class Clock extends Date { static now() { return fixture.now; } }
  const document = {
    hidden: false,
    querySelector: (selector) => element(selector.slice(1)),
    createElement: (tag) => new Element(tag),
    addEventListener: (name, handler) => events.addEventListener(name, handler),
  };
  const context = vm.createContext({
    console, Date: Clock, URLSearchParams, document,
    window: { matchMedia: () => ({ matches: false }) },
    setTimeout(handler, delay) {
      timers.set(++timerID, { handler, delay });
      return timerID;
    },
    clearTimeout: (id) => timers.delete(id),
    backendFetch: async (path) => { requests.push(path); return fixture.route(path); },
    syncChildren(parent, children) {
      const keep = new Set(children);
      for (const child of [...parent.children]) if (!keep.has(child)) child.remove();
      children.forEach((child, index) => {
        if (parent.children[index] !== child) parent.insertBefore(child, parent.children[index] || null);
      });
    },
    consoleUIIcons() {},
    consoleSelectView: (view) => fixture.run(`insightsViewChanged(${JSON.stringify(view)});
      if (typeof imageReviewViewChanged === "function") imageReviewViewChanged(${JSON.stringify(view)});`),
  });
  fixture.document = document;
  fixture.run = (code) => vm.runInContext(code, context);
  fixture.connect = async (view = "overview") => {
    fixture.run(`insights.view = "${view}";
      if (typeof imageReviewViewChanged === "function") imageReviewViewChanged("${view}");
      insightsConnectionChanged(true);`);
    await settle();
  };
  vm.runInContext(source, context);
  if (images) {
    vm.runInContext(fs.readFileSync(
      new URL("../internal/server/static/image-review.js", import.meta.url), "utf8",
    ), context);
    fixture.question = {
      ...fixture.question, title: "Question [image_1]", options: "A. one\nB. [image_1]",
      state: "error", stage: "needs_review", image_count: 1, validation: "candidate",
      needs_review: true, error: "AI_VISION_DISAGREEMENT", answers: [],
      images: [{ id: "image_1", sha256: "b".repeat(64), mime_type: "image/png", byte_count: 1 }],
      diagnostics: {
        solver_answers: ["B. [image_1]"], verifier_answers: ["A. one"], agreement: false,
        readings: [{ id: "image_1", text: "x + 2" }],
        verification: { readable: true, reading_matches: true },
        stages: [{ stage: "solving", status: "ok", model: "deepseek-search", seconds: 8, attempt: 1 }],
      },
    };
    const route = fixture.route;
    fixture.list = () => ({
      items: [fixture.question], total: 1, pages: 1, page: 1, page_size: 20,
      counts: { total: 1, review: 1, active: 0, done: 0 },
      courses: [{ id: "101", title: "Offline course", entries: 1 }], sampled_at: 1700000000,
    });
    fixture.route = (path) => {
      if (path.startsWith("/image-questions?")) return fixture.list();
      if (path.endsWith("/images/image_1")) return {
        ...fixture.question.images[0], data_url: "data:image/png;base64,eA==",
      };
      if (path.startsWith("/image-questions/")) return { ...fixture.question, id: path.split("/")[2] };
      return route(path);
    };
  }
  return fixture;
}

test("overview samples the backend host and bank only after connecting", async () => {
  const f = harness();
  assert.equal(f.requests.length, 0);
  await f.connect();
  assert.deepEqual(f.requests, ["/resources", "/question-bank/stats"]);
  assert.equal(f.element("resource-cpu").textContent, "25.5%");
  assert.equal(f.element("resource-memory-note").textContent, "1.0 GiB / 2.0 GiB");
  assert.equal(f.element("resource-system").textContent, "25.0%");
  assert.equal(f.element("resource-disk").textContent, "10.0%");
  assert.equal(f.element("resource-disk-label").textContent, "数据盘");
  assert.equal(f.element("resource-data-path").textContent, "/data/chaoxing/backend");
  assert.match(f.element("resource-system-detail").textContent, /\/dev\/vda1/);
  assert.match(f.element("resource-disk-detail").textContent, /\/dev\/vdb1 · \/data/);
  assert.equal(f.element("resource-cpu-bar").attributes.get("aria-valuenow"), "25.5");
  assert.equal(f.element("resource-disk-bar").attributes.get("style:--progress"), "10%");
  assert.equal(f.element("resource-load").textContent, "0.50 / 0.40 / 0.30");
  assert.equal(f.element("resource-swap").textContent, "未启用");
  assert.equal(f.element("overview-bank-content").textContent, "150 B");
  assert.equal(f.element("overview-bank-database").textContent, "1.0 MiB");
  assert.ok([...f.timers.values()].some((timer) => timer.delay === 5000));
});

test("missing host metrics are not displayed as zero usage", async () => {
  const f = harness();
  f.resources = { status: "partial", sampled_at: 1700000000, cpu: { percent: null } };
  await f.connect();
  assert.equal(f.element("resource-cpu").textContent, "--");
  assert.equal(f.element("resource-cpu-bar").attributes.get("data-state"), "unknown");
  assert.equal(f.element("resource-cpu-bar").attributes.has("aria-valuenow"), false);
  assert.equal(f.element("resource-memory").textContent, "--");
  assert.equal(f.element("resource-network").textContent, "--");
  assert.match(f.element("resource-status").textContent, /部分指标不可用/);
});

test("shared system storage is never shown as a separate data disk", async () => {
  const f = harness();
  f.resources.data_disk_shared = true;
  f.resources.disks = [f.resources.disks[0]];
  f.resources.data_directory = "/var/lib/chaoxing-backend";
  await f.connect();
  assert.equal(f.element("resource-disk").textContent, "共用");
  assert.equal(f.element("resource-disk-note").textContent, "与系统盘共用");
  assert.equal(f.element("resource-disk-bar").attributes.has("aria-valuenow"), false);
  assert.equal(f.element("resource-disk-bar").attributes.get("aria-valuetext"), "与系统盘共用");
  assert.doesNotMatch(f.element("resource-host").textContent, /数据盘/);
});

test("legacy resource APIs do not invent system disk statistics", async () => {
  const f = harness();
  delete f.resources.disks;
  delete f.resources.data_disk_shared;
  delete f.resources.data_directory;
  await f.connect();
  assert.equal(f.element("resource-system").textContent, "--");
  assert.equal(f.element("resource-disk").textContent, "25.0%");
  assert.equal(f.element("resource-disk-label").textContent, "数据所在磁盘");
  assert.equal(f.element("resource-data-path").textContent, "--");
});

test("rings use bounded values, alert levels, and preserve their mounted elements", async () => {
  const f = harness();
  await f.connect();
  const ring = f.element("resource-cpu-bar");
  f.resources.cpu.percent = 85;
  await f.run("loadResources(true)");
  assert.equal(f.element("resource-cpu-bar"), ring);
  assert.equal(ring.attributes.get("data-level"), "warning");
  assert.equal(ring.attributes.get("style:--progress"), "85%");
  f.resources.cpu.percent = 99;
  await f.run("loadResources(true)");
  assert.equal(ring.attributes.get("data-level"), "critical");
  for (const value of [null, NaN, -1, 101]) {
    f.resources.cpu.percent = value;
    await f.run("loadResources(true)");
    assert.equal(f.element("resource-cpu").textContent, "--");
    assert.equal(ring.attributes.has("aria-valuenow"), false);
    assert.equal(ring.attributes.get("style:--progress"), "0%");
  }
});

test("one-second job refresh hooks do not cause one-second resource scans", async () => {
  const f = harness();
  await f.connect();
  for (let index = 0; index < 10; index++) f.run("insightsConnectionChanged(true)");
  f.now += 4999;
  await f.run("refreshInsights()");
  assert.equal(f.requests.length, 2);
  f.now += 2;
  await f.run("refreshInsights()");
  assert.equal(f.requests.length, 4);
});

test("hidden pages poll less often and unrelated views do not poll insights", async () => {
  const f = harness();
  await f.connect();
  f.document.hidden = true;
  f.events.fire("visibilitychange");
  assert.equal(f.timers.size, 1);
  assert.equal([...f.timers.values()][0].delay, 20000);
  f.run('insightsViewChanged("tasks")');
  assert.equal(f.timers.size, 0);
  f.now += 30000;
  f.run('insightsViewChanged("bank")');
  await settle();
  assert.equal(f.requests.filter((path) => path === "/resources").length, 1);
  assert.equal(f.requests.filter((path) => path.startsWith("/question-bank?")).length, 1);
});

test("question filters are safely encoded, debounced, and reset to page one", async () => {
  const f = harness();
  await f.connect("bank");
  f.run("insights.page = 3");
  f.element("bank-search").value = "中文 & A..B? '";
  f.element("bank-search").fire("input");
  f.element("bank-search").value = "中文 & A..B? _";
  f.element("bank-search").fire("input");
  assert.equal(f.run("insights.page"), 1);
  const pending = [...f.timers.values()].filter((timer) => timer.delay === 300);
  assert.equal(pending.length, 1);
  await pending[0].handler();
  await settle();
  const query = new URLSearchParams(f.requests.at(-1).split("?")[1]);
  assert.equal(query.get("search"), "中文 & A..B? _");
  assert.equal(query.get("page"), "1");
  assert.equal(query.get("page_size"), "20");
  f.element("bank-type").value = "judgement";
  f.element("bank-type").fire("change");
  await settle();
  assert.equal(new URLSearchParams(f.requests.at(-1).split("?")[1]).get("type"), "judgement");
});

test("late results for an old search never overwrite the newer list", async () => {
  const f = harness();
  await f.connect("bank");
  let release;
  f.route = (path) => {
    const search = new URLSearchParams(path.split("?")[1]).get("search");
    if (search === "old") return new Promise((resolve) => { release = resolve; });
    return { items: [{ ...f.question, title: "New search result" }], total: 1, pages: 1 };
  };
  f.element("bank-search").value = "old";
  const oldRequest = f.run("loadBankList(true)");
  f.element("bank-search").value = "new";
  f.element("bank-filters").fire("submit");
  await settle();
  release({ items: [{ ...f.question, title: "Old stale result" }], total: 1, pages: 1 });
  await oldRequest;
  const row = f.element("bank-list").children[0];
  assert.equal(row.children[0].children[1].textContent, "New search result");
  assert.equal(f.element("bank-list").attributes.get("aria-busy"), "false");
});

test("data refresh preserves the mounted rows, filters, and open details", async () => {
  const f = harness();
  await f.connect("bank");
  f.element("bank-search").value = "Saved search";
  await f.run(`openBankDetail("${questionID}")`);
  const row = f.element("bank-list").children[0];
  const option = f.element("bank-course").children[1];
  const detail = f.element("bank-detail-body").children;
  await f.run("refreshInsights(true)");
  assert.equal(f.element("bank-list").children[0], row);
  assert.equal(f.element("bank-course").children[1], option);
  assert.equal(f.element("bank-search").value, "Saved search");
  assert.equal(f.element("bank-detail").open, true);
  assert.equal(f.element("bank-detail-body").children, detail);
});

test("failed resource and bank refreshes keep last successful data", async () => {
  const f = harness();
  await f.connect();
  f.route = () => { throw new Error("Offline failure"); };
  await f.run("refreshInsights(true)");
  assert.equal(f.element("resource-cpu").textContent, "25.5%");
  assert.equal(f.element("resource-error").hidden, false);
  assert.equal(f.element("overview-bank-entries").textContent, "1");
  assert.equal(f.element("bank-error").hidden, false);
  const g = harness();
  await g.connect("bank");
  const row = g.element("bank-list").children[0];
  g.route = () => { throw new Error("Offline list failure"); };
  await g.run("refreshInsights(true)");
  assert.equal(g.element("bank-list").children[0], row);
  assert.equal(g.element("bank-results").textContent, "列表未更新");
});

test("pagination follows server totals and disables controls during loading", async () => {
  const f = harness();
  await f.connect("bank");
  f.run("insights.pages = 3; updateBankPagination()");
  assert.equal(f.element("bank-prev").disabled, true);
  assert.equal(f.element("bank-next").disabled, false);
  let release;
  f.route = () => new Promise((resolve) => { release = resolve; });
  f.element("bank-next").fire("click");
  assert.equal(f.run("insights.page"), 2);
  assert.equal(f.element("bank-next").disabled, true);
  release({ items: [f.question], total: 41, pages: 3 });
  await settle();
  assert.equal(f.element("bank-prev").disabled, false);
  assert.equal(f.element("bank-page").textContent, "第 2 / 3 页");
});

test("empty or shrinking results clamp the page without discarding filters", async () => {
  const f = harness();
  await f.connect("bank");
  f.element("bank-search").value = "keep";
  f.run("insights.page = 9");
  f.route = () => ({ items: [], total: 0, pages: 0 });
  await f.run("loadBankList(true)");
  await settle();
  assert.equal(f.run("insights.page"), 1);
  assert.equal(f.element("bank-empty").hidden, false);
  assert.equal(f.element("bank-search").value, "keep");
  assert.equal(f.element("bank-next").disabled, true);
});

test("details render question text, option arrays, answers and metadata as text", async () => {
  const f = harness();
  f.question.title = "<script>untrusted question</script>";
  f.question.options = ["A. <img src=x>", "B. 2"];
  await f.connect("bank");
  await f.run(`openBankDetail("${questionID}")`);
  assert.equal(f.element("bank-detail").open, true);
  assert.equal(f.element("bank-detail-title").textContent, f.question.title);
  assert.equal(f.element("bank-detail-options").textContent, "A. <img src=x>\nB. 2");
  assert.equal(f.element("bank-detail-answers").children[0].textContent, "B. 2");
  assert.equal(f.element("bank-detail-meta").children.length, 8);
  assert.equal(f.element("bank-detail-body").hidden, false);
});

test("image questions render stem, choices and answers with one authenticated request per image", async () => {
  const f = harness();
  const image = { id: "image_1", sha256: "b".repeat(64), mime_type: "image/png", byte_count: 1 };
  f.question = {
    ...f.question, title: "Stem [image_1]", options: "A. [image_1]\nB. two",
    answers: ["A. [image_1]"], image_count: 1, images: [image],
  };
  const route = f.route;
  f.route = (path) => path.endsWith("/images/image_1")
    ? { ...image, data_url: "data:image/png;base64,eA==" } : route(path);
  f.stats.storage.image_count = 1;
  f.stats.storage.image_bytes = 1;
  await f.connect("bank");
  await f.run(`openBankDetail("${questionID}")`);
  await settle();
  assert.equal(f.requests.filter((path) => path.endsWith("/images/image_1")).length, 1);
  assert.match(f.element("bank-list").children[0].children[0].children[0].children[0].textContent, /1 图/);
  assert.match(f.element("bank-content-note").textContent, /图片 1 张 \/ 1 B/);
  for (const element of [
    f.element("bank-detail-title"), f.element("bank-detail-options"),
    f.element("bank-detail-answers").children[0],
  ]) {
    const slot = element.children.find((child) => child.className === "bank-image");
    assert.equal(slot.children[0].tag, "img");
    assert.equal(slot.children[0].src, "data:image/png;base64,eA==");
  }
  assert.equal(f.element("bank-detail-meta").children.length, 9);
});

test("image quality separates cross-checked results from historical candidates", async () => {
  for (const [fields, expected, reusable] of [
    [{ validation: "format_checked", reusable: false }, "仅格式检查 · 不复用", false],
    [{ validation: "vision_cross_checked", reusable: true }, "原图复核 · 两路一致", true],
    [{ historical: true, validation: "format_checked", reusable: false }, "历史候选 · 不复用", false],
  ]) {
    const f = harness();
    f.question = { ...f.question, image_count: 1, ...fields };
    await f.connect("bank");
    assert.equal(f.run(`bankQuality(${JSON.stringify(f.question)})`), expected);
    assert.match(f.run(`insights.rows.get("${questionID}").updated.textContent`), new RegExp(expected));
    await f.run(`openBankDetail("${questionID}")`);
    const metadata = Object.fromEntries(f.element("bank-detail-meta").children.map(
      (row) => row.children.map((child) => child.textContent),
    ));
    assert.equal(metadata["答案检查"], expected);
    assert.equal(metadata["题库复用"], reusable ? "可复用" : "不复用");
    assert.equal(f.element("bank-detail-validation").textContent, expected);
  }
});

test("missing or invalid image responses remain explicit failures without inserting remote HTML", async () => {
  for (const mode of ["missing", "remote", "hash"]) {
    const f = harness();
    const image = { id: "image_1", sha256: "b".repeat(64), mime_type: "image/png", byte_count: 1 };
    f.question = { ...f.question, title: "[image_1]", image_count: 1, images: mode === "missing" ? [] : [image] };
    const route = f.route;
    f.route = (path) => path.endsWith("/images/image_1")
      ? { ...image, sha256: mode === "hash" ? "bad" : image.sha256, data_url: "https://untrusted.invalid/x" }
      : route(path);
    await f.connect("bank");
    await f.run(`openBankDetail("${questionID}")`);
    await settle();
    const slot = f.element("bank-detail-title").children[0];
    assert.match(slot.textContent, /图片读取失败/);
    assert.equal(slot.children.length, 0);
  }
});

test("closing image details ignores late image loads and normal refresh does not reload images", async () => {
  const f = harness();
  const image = { id: "image_1", sha256: "b".repeat(64), mime_type: "image/png", byte_count: 1 };
  f.question = { ...f.question, title: "[image_1]", image_count: 1, images: [image] };
  const route = f.route;
  let release;
  f.route = (path) => path.endsWith("/images/image_1")
    ? new Promise((resolve) => { release = resolve; }) : route(path);
  await f.connect("bank");
  await f.run(`openBankDetail("${questionID}")`);
  const slot = f.element("bank-detail-title").children[0];
  await f.run("refreshInsights(true)");
  assert.equal(f.requests.filter((path) => path.endsWith("/images/image_1")).length, 1);
  f.element("bank-detail-close").fire("click");
  release({ ...image, data_url: "data:image/png;base64,eA==" });
  await settle();
  assert.equal(slot.children.length, 0);
  assert.match(slot.textContent, /正在读取/);
});

test("closing or disconnecting a detail request prevents late updates", async () => {
  const f = harness();
  await f.connect("bank");
  let release;
  f.route = () => new Promise((resolve) => { release = resolve; });
  const request = f.run(`openBankDetail("${questionID}")`);
  f.element("bank-detail-close").fire("click");
  release(f.question);
  await request;
  assert.equal(f.element("bank-detail").open, false);
  assert.equal(f.element("bank-detail-body").hidden, true);
  const second = f.run(`openBankDetail("${questionID}")`);
  f.run("insightsConnectionChanged(false)");
  release(f.question);
  await second;
  assert.equal(f.element("bank-detail").open, false);
  assert.equal(f.element("bank-list").children.length, 0);
  assert.equal(f.timers.size, 0);
});

test("a connection error retains samples but an explicit disconnect clears them", async () => {
  const f = harness();
  await f.connect();
  f.run("insightsConnectionChanged(false, true)");
  assert.equal(f.element("resource-cpu").textContent, "25.5%");
  assert.match(f.element("resource-status").textContent, /保留上次数据/);
  assert.equal(f.element("resource-refresh").disabled, true);
  f.run("insightsConnectionChanged(false)");
  assert.equal(f.element("resource-cpu").textContent, "--");
  assert.equal(f.element("overview-bank-content").textContent, "--");
});

test("clear filters resets query values and course selection without changing page size", async () => {
  const f = harness();
  await f.connect("bank");
  f.element("bank-search").value = "test";
  f.element("bank-course").value = "101";
  f.element("bank-type").value = "single";
  f.element("bank-sort").value = "hits";
  f.element("bank-page-size").value = "50";
  f.element("bank-clear").fire("click");
  await settle();
  const query = new URLSearchParams(f.requests.at(-1).split("?")[1]);
  assert.equal(query.get("search"), "");
  assert.equal(query.get("course_id"), "");
  assert.equal(query.get("type"), "");
  assert.equal(query.get("sort"), "updated");
  assert.equal(query.get("page_size"), "50");
});

test("image review has independent navigation and five/twenty second visible/hidden polling", async () => {
  const f = harness({ images: true });
  assert.match(markup, /id="images-tab"/);
  assert.match(markup, /id="images-view"/);
  await f.connect("images");
  assert.equal(f.requests.filter((path) => path.startsWith("/image-questions?")).length, 1);
  assert.ok([...f.timers.values()].some((timer) => timer.delay === 5000));
  f.document.hidden = true;
  f.events.fire("visibilitychange");
  assert.deepEqual([...f.timers.values()].map((timer) => timer.delay), [20000]);
  f.run('consoleSelectView("tasks")');
  assert.equal(f.timers.size, 0);
  f.run('consoleSelectView("images")');
  await settle();
  assert.equal(f.requests.filter((path) => path.startsWith("/image-questions?")).length, 2);
});

test("image review preserves selection, mounted originals and open zoom across list refreshes", async () => {
  const f = harness({ images: true });
  await f.connect("images");
  const row = f.element("images-list").children[0];
  const title = f.element("images-detail-title").children;
  const option = f.element("images-course").children[1];
  const preview = title.find((child) => child.className === "bank-image").children[0];
  preview.fire("click");
  f.element("image-zoom-in").fire("click");
  await f.run("loadImageReview()");
  await settle();
  assert.equal(f.element("images-list").children[0], row);
  assert.equal(f.element("images-detail-title").children, title);
  assert.equal(f.element("images-course").children[1], option);
  assert.equal(f.requests.filter((path) => path.endsWith("/images/image_1")).length, 1);
  assert.equal(f.element("image-original-dialog").open, true);
  assert.equal(f.element("image-zoom-value").textContent, "125%");
  assert.equal(row.children[0].attributes.get("aria-pressed"), "true");
});

test("image review shows failures, readings and both AI candidates as text", async () => {
  const f = harness({ images: true });
  f.question.title = "<script>unsafe</script> [image_1]";
  await f.connect("images");
  const answers = f.element("images-detail-answers").children;
  assert.equal(answers[0].lastElementChild.textContent, "暂无结果");
  assert.equal(answers[1].children[0].textContent, "DeepSeek 求解");
  assert.equal(answers[2].lastElementChild.textContent, "A. one");
  assert.equal(f.element("images-failure-section").hidden, false);
  assert.match(f.element("images-detail-failure").textContent, /不一致/);
  assert.equal(f.element("images-detail-failure-code").textContent, "AI_VISION_DISAGREEMENT");
  assert.equal(f.element("images-detail-readings").children[0].children[1].textContent, "x + 2");
  assert.match(f.element("images-detail-checks").textContent, /答案一致：未通过/);
  assert.equal(f.element("images-detail-title").children[0].textContent, "<script>unsafe</script> ");
  assert.equal(f.run('imageQuality({validation:"vision_cross_checked"})'), "两路一致 · 未人工确认");
});

test("image review explains upstream rate limits in both the list and detail", async () => {
  const f = harness({ images: true });
  f.question.error = "AI_RATE_LIMITED";
  f.question.answer_preview = "";
  await f.connect("images");
  assert.match(f.element("images-list").children[0].children[0].children[3].textContent, /限流/);
  assert.match(f.element("images-detail-failure").textContent, /限流/);
  assert.equal(f.element("images-detail-failure-code").textContent, "AI_RATE_LIMITED");
});

test("pending image detail is deduplicated and refreshed when its state version changes", async () => {
  const f = harness({ images: true });
  const route = f.route;
  let release;
  f.route = (path) => path === `/image-questions/${questionID}`
    ? new Promise((resolve) => { release = resolve; }) : route(path);
  await f.connect("images");
  await f.run("loadImageReview()");
  assert.equal(f.requests.filter((path) => path === `/image-questions/${questionID}`).length, 1);
  release(f.question);
  await settle();
  f.route = route;
  f.question.updated_at++;
  await f.run("loadImageReview()");
  await settle();
  assert.equal(f.requests.filter((path) => path === `/image-questions/${questionID}`).length, 2);
});

test("late image list and detail responses never overwrite a newer search or selection", async () => {
  const f = harness({ images: true });
  await f.connect("images");
  const route = f.route;
  let releaseList, releaseDetail;
  f.route = (path) => {
    if (path.startsWith("/image-questions?") && path.includes("search=old")) {
      return new Promise((resolve) => { releaseList = resolve; });
    }
    if (path === `/image-questions/${"c".repeat(32)}`) {
      return new Promise((resolve) => { releaseDetail = resolve; });
    }
    return route(path);
  };
  f.element("images-search").value = "old";
  const oldList = f.run("loadImageReview()");
  f.question.title = "New result [image_1]";
  f.element("images-search").value = "new";
  f.element("images-filters").fire("submit");
  await settle();
  releaseList({ ...f.list(), items: [{ ...f.question, title: "Stale result" }] });
  await oldList;
  assert.equal(f.element("images-list").children[0].children[0].children[1].textContent, f.question.title);
  f.run(`imageReview.rows.set("${"c".repeat(32)}", {
    button: document.createElement("button"), question: {id: "${"c".repeat(32)}"}
  }); selectImageQuestion("${"c".repeat(32)}"); selectImageQuestion("${questionID}");`);
  await settle();
  const title = f.element("images-detail-title").children;
  releaseDetail({ ...f.question, id: "c".repeat(32), title: "Stale detail" });
  await settle();
  assert.equal(f.element("images-detail-title").children, title);
});

test("late original image responses are ignored after selection changes", async () => {
  const f = harness({ images: true });
  const route = f.route;
  let release;
  f.route = (path) => path.endsWith("/images/image_1")
    ? new Promise((resolve) => { release = resolve; }) : route(path);
  await f.connect("images");
  const slot = f.element("images-detail-title").children.find((child) => child.className === "bank-image");
  f.run('selectImageQuestion("")');
  release({ ...f.question.images[0], data_url: "data:image/png;base64,eA==" });
  await settle();
  assert.equal(slot.children.length, 0);
});

test("image failures retain data, later polls retry details and manual refresh retries images", async () => {
  const f = harness({ images: true });
  await f.connect("images");
  const route = f.route;
  const row = f.element("images-list").children[0];
  f.route = () => { throw new Error("Offline"); };
  await f.run("loadImageReview()");
  assert.equal(f.element("images-list").children[0], row);
  assert.match(f.element("images-status").textContent, /保留上次数据/);
  f.route = (path) => path === `/image-questions/${questionID}`
    ? Promise.reject(new Error("Detail offline")) : route(path);
  f.run('imageReview.detailVersion = ""');
  await f.run("loadImageReview()");
  await settle();
  assert.equal(f.element("images-detail-error").hidden, false);
  f.route = route;
  await f.run("loadImageReview()");
  await settle();
  assert.equal(f.element("images-detail-error").hidden, true);
  const before = f.requests.filter((path) => path.endsWith("/images/image_1")).length;
  f.element("images-refresh").fire("click");
  await settle();
  assert.equal(f.requests.filter((path) => path.endsWith("/images/image_1")).length, before + 1);
});

test("image search debounce, filters, pagination and clear follow server totals", async () => {
  const f = harness({ images: true });
  await f.connect("images");
  f.element("images-search").value = "中文 & ?";
  f.element("images-search").fire("input");
  f.element("images-search").value = "中文 & _";
  f.element("images-search").fire("input");
  const pending = [...f.timers.values()].filter((timer) => timer.delay === 300);
  assert.equal(pending.length, 1);
  await pending[0].handler();
  await settle();
  let query = new URLSearchParams(f.requests.findLast((path) => path.includes("?")).split("?")[1]);
  assert.equal(query.get("search"), "中文 & _");
  f.list = () => ({ items: [f.question], total: 42, pages: 3 });
  f.element("images-filter-status").value = "error";
  f.element("images-filter-status").fire("change");
  await settle();
  f.element("images-next").fire("click");
  await settle();
  assert.equal(f.element("images-page").textContent, "第 2 / 3 页");
  f.element("images-clear").fire("click");
  await settle();
  query = new URLSearchParams(f.requests.findLast((path) => path.includes("?")).split("?")[1]);
  for (const key of ["search", "course_id", "status"]) assert.equal(query.get(key), "");
  assert.equal(query.get("page"), "1");
  f.run("imageReview.page = 9");
  f.list = () => ({ items: [], total: 0, pages: 0 });
  await f.run("loadImageReview()");
  await settle();
  assert.equal(f.run("imageReview.page"), 1);
  assert.equal(f.element("images-empty").hidden, false);
});

test("image review connection loss retains data but explicit disconnect clears sensitive images", async () => {
  const f = harness({ images: true });
  await f.connect("images");
  f.run('openImageOriginal({id:"image_1",data_url:"data:image/png;base64,eA=="})');
  f.run("insightsConnectionChanged(false, true)");
  assert.equal(f.element("images-list").children.length, 1);
  assert.equal(f.element("images-refresh").disabled, true);
  assert.equal(f.timers.size, 0);
  f.run("insightsConnectionChanged(false)");
  assert.equal(f.element("images-list").children.length, 0);
  assert.equal(f.element("images-detail-body").hidden, true);
  assert.equal(f.element("image-original-dialog").open, false);
});

test("original viewer zoom is bounded and preview endpoints never mutate course jobs", async () => {
  const f = harness({ images: true });
  await f.connect("images");
  f.run("setImageZoom(900)");
  assert.equal(f.element("image-original").style.width, "400%");
  assert.equal(f.element("image-zoom-in").disabled, true);
  f.run("setImageZoom(-1)");
  assert.equal(f.element("image-original").style.width, "50%");
  assert.equal(f.element("image-zoom-out").disabled, true);
  assert.ok(f.requests.every((path) => path.startsWith("/image-questions")));
});
