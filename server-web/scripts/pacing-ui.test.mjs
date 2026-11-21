import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = fs.readFileSync(
  new URL("../internal/server/static/pacing.js", import.meta.url), "utf8",
);

class Element {
  constructor(tag = "div") {
    this.tag = tag;
    this.children = [];
    this.listeners = new Map();
    this.attributes = new Map();
    this.dataset = {};
    this.style = {};
    this.title = "";
    this.textContent = "";
    this.className = "";
    this.hidden = false;
    this.disabled = false;
    this.parentElement = null;
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
  replaceChildren(...items) {
    this.children = [];
    this.append(...items);
  }
  querySelectorAll(selector) {
    assert.match(selector, /^\./, `unsupported selector ${selector}`);
    const name = selector.slice(1);
    const found = [];
    const walk = (node) => {
      for (const child of node.children) {
        if (String(child.className).split(/\s+/).includes(name)) found.push(child);
        walk(child);
      }
    };
    walk(this);
    return found;
  }
  querySelector() { return null; }
}

const selectors = [
  "#pacing-clock", "#pacing-zone", "#pacing-flag", "#pacing-now", "#pacing-next",
  "#pacing-state", "#pacing-error", "#pacing-refresh", "#pacing-track", "#pacing-windows",
  "#pacing-gaps", "#pacing-note", "#pacing-resting", "#pacing-resting-empty",
];

function harness() {
  const elements = new Map(selectors.map((selector) => [selector, new Element()]));
  const intervals = new Map();
  let intervalID = 0;
  const requests = [];
  let responder = async () => ({ ok: true, status: 200, json: async () => ({}) });
  const context = vm.createContext({
    console, Date, AbortSignal, Math, JSON, Number, String, Array, Object, Boolean,
    document: {
      querySelector: (selector) => {
        assert.ok(elements.has(selector), `Missing fixture element ${selector}`);
        return elements.get(selector);
      },
      createElement: (tag) => new Element(tag),
      addEventListener() {},
    },
    setInterval: (handler, delay) => {
      intervalID += 1;
      intervals.set(intervalID, { handler, delay });
      return intervalID;
    },
    clearInterval: (id) => intervals.delete(id),
    fetch: async (url, options = {}) => {
      requests.push({ url, options });
      return responder(url, options);
    },
  });
  new vm.Script(source, { filename: "pacing.js" }).runInContext(context);
  return {
    context, elements, intervals, requests,
    el: (selector) => elements.get(selector),
    text: (selector) => elements.get(selector).textContent,
    respond(handler) { responder = handler; },
  };
}

const NOW = 1792000000;

function window(name, label, start, end, options = {}) {
  return {
    name, label, start, end,
    start_minute: options.start_minute ?? 0,
    end_minute: options.end_minute ?? 0,
    wraps: Boolean(options.wraps),
    chance: options.chance ?? 0.7,
    cap_minutes: options.cap_minutes ?? 60,
    active: Boolean(options.active),
    starts_in_minutes: options.starts_in_minutes ?? 120,
  };
}

function snapshot(overrides = {}) {
  return {
    clock: { now: NOW, time: "12:10", date: "2026-10-22", minute_of_day: 730, timezone: "Asia/Shanghai" },
    config: { enabled: true, exit_on_long_pause: true, exit_after: 300, timezone: "Asia/Shanghai" },
    windows: [
      window("lunch", "午饭", "11:40", "13:00",
        { active: true, start_minute: 700, end_minute: 780, chance: 0.7, cap_minutes: 60 }),
      window("dinner", "晚饭", "17:30", "19:00",
        { start_minute: 1050, end_minute: 1140, chance: 0.7, cap_minutes: 60, starts_in_minutes: 320 }),
      window("night", "夜间休息", "23:30", "07:00",
        { wraps: true, start_minute: 1410, end_minute: 420, chance: 1, cap_minutes: 90, starts_in_minutes: 680 }),
    ],
    gaps: {
      answer: [1, 15], job: [2, 18], chapter: [8, 45], micro: [60, 300],
      course: [120, 600], group: [600, 1800], start: [10, 120],
    },
    labels: {
      windows: { lunch: "午饭", dinner: "晚饭", night: "夜间休息" },
      gaps: {
        answer: "看题思考", job: "切换任务点", chapter: "翻到下一节", micro: "离开一下",
        course: "换一门课", group: "这组刷完了歇一会", start: "开始学习",
      },
    },
    group_size: [2, 3],
    micro_break_chance: 0.05,
    resting: [],
    ...overrides,
  };
}

function jsonHandler(body) {
  const payload = { pacing: { ...body }, resting: body.resting || [] };
  delete payload.pacing.resting;
  return async () => ({ ok: true, status: 200, json: async () => payload });
}

test("windows render with times, odds and current state", async () => {
  const f = harness();
  f.respond(jsonHandler(snapshot()));
  await f.context.loadPacing();
  const items = f.el("#pacing-windows").children;
  assert.equal(items.length, 3);
  assert.deepEqual(items.map((item) => item.dataset.name), ["lunch", "dinner", "night"]);
  const [lunch, dinner] = items;
  assert.equal(lunch.dataset.active, "true");
  assert.equal(lunch.children[0].textContent, "午饭");
  assert.equal(lunch.children[1].textContent, "11:40–13:00");
  assert.equal(lunch.children[2].textContent, "70% 概率 · 最长 60 分钟");
  assert.equal(lunch.children[3].textContent, "进行中");
  assert.equal(dinner.dataset.active, "false");
  assert.match(dinner.children[3].textContent, /后$/);
});

test("timeline splits a wrapping night window and marks the current minute", async () => {
  const f = harness();
  f.respond(jsonHandler(snapshot()));
  await f.context.loadPacing();
  const children = f.el("#pacing-track").children;
  const bands = children.filter((child) => child.className === "pacing-band");
  assert.equal(bands.length, 4);
  assert.deepEqual(bands.map((band) => band.dataset.name), ["lunch", "dinner", "night", "night"]);
  const night = bands.filter((band) => band.dataset.name === "night");
  assert.equal(night[0].style.left, "97.91666666666666%");
  assert.equal(night[1].style.left, "0%");
  const marker = children.find((child) => child.className === "pacing-now-line");
  assert.ok(marker);
  assert.equal(marker.style.left, `${(730 / 1440) * 100}%`);
  const ticks = children.filter((child) => child.className === "pacing-tick");
  assert.deepEqual(ticks.map((tick) => tick.textContent), ["00:00", "06:00", "12:00", "18:00", "24:00"]);
});

test("gaps table shows every scenario with a readable range", async () => {
  const f = harness();
  f.respond(jsonHandler(snapshot()));
  await f.context.loadPacing();
  const rows = f.el("#pacing-gaps").children;
  assert.equal(rows.length, 7);
  const answer = rows.find((row) => row.dataset.key === "answer");
  assert.equal(answer.children[0].textContent, "看题思考");
  assert.equal(answer.children[1].textContent, "1 秒–15 秒");
  const group = rows.find((row) => row.dataset.key === "group");
  assert.equal(group.children[1].textContent, "10 分钟–30 分钟");
  assert.match(f.text("#pacing-note"), /每 2–3 门课一组/);
  assert.match(f.text("#pacing-note"), /退出登录/);
});

test("resting jobs show a countdown and the soonest one comes first", async () => {
  const f = harness();
  f.respond(jsonHandler(snapshot({
    resting: [
      {
        id: "job-b", account: "账号乙", course: "大学生军事技能训练", chapter: "2.1",
        reason: "夜间休息时间，模拟休息", kind: "window", until: NOW + 7200, minutes: 120,
      },
      {
        id: "job-a", account: "账号甲", course: "军事理论", chapter: "1.1 第一节",
        reason: "午饭时间，模拟休息，约 50 分钟", kind: "window", until: NOW + 3000, minutes: 50,
      },
    ],
  })));
  await f.context.loadPacing();
  const rows = f.el("#pacing-resting").children;
  assert.deepEqual(rows.map((row) => row.dataset.jobId), ["job-a", "job-b"]);
  assert.equal(rows[0].dataset.kind, "window");
  assert.equal(rows[0].children[0].children[0].textContent, "账号甲");
  assert.equal(rows[0].children[0].children[1].textContent, "午饭时间，模拟休息，约 50 分钟");
  assert.equal(rows[0].children[1].textContent, "军事理论 · 1.1 第一节");
  assert.match(rows[0].children[2].textContent, /^还剩 50 分钟（预计 \d\d:\d\d 结束）$/);
  assert.equal(f.el("#pacing-resting-empty").hidden, true);
});

test("an empty rest list hides nothing but shows the placeholder", async () => {
  const f = harness();
  f.respond(jsonHandler(snapshot({ resting: [] })));
  await f.context.loadPacing();
  assert.equal(f.el("#pacing-resting").children.length, 0);
  assert.equal(f.el("#pacing-resting-empty").hidden, false);
});

test("hero explains the active rest window and the next one", async () => {
  const f = harness();
  f.respond(jsonHandler(snapshot()));
  await f.context.loadPacing();
  assert.equal(f.text("#pacing-clock"), "12:10");
  assert.equal(f.text("#pacing-zone"), "节奏时区 Asia/Shanghai");
  assert.match(f.text("#pacing-now"), /现在是午饭时间（11:40–13:00）/);
  assert.match(f.text("#pacing-next"), /70% 的任务会停下来休息，最长 60 分钟/);
  assert.equal(f.text("#pacing-flag"), "已开启");
  assert.equal(f.el("#pacing-flag").dataset.state, "on");
  assert.equal(f.el("#pacing-error").hidden, true);
});

test("outside a rest window the page points at the next one", async () => {
  const f = harness();
  const data = snapshot({ clock: { now: NOW, time: "15:10", date: "2026-10-22", minute_of_day: 910, timezone: "Asia/Shanghai" } });
  data.windows[0].active = false;
  data.windows[0].starts_in_minutes = 1380;
  data.windows[1].starts_in_minutes = 140;
  f.respond(jsonHandler(data));
  await f.context.loadPacing();
  assert.equal(f.text("#pacing-now"), "现在不在休息时段，任务按随机间隔继续");
  assert.match(f.text("#pacing-next"), /下一段：晚饭 17:30，还有 2 小时 20 分钟/);
});

test("a disabled schedule says so instead of a countdown", async () => {
  const f = harness();
  const data = snapshot({ config: { enabled: false, exit_on_long_pause: false, exit_after: 300 } });
  data.windows = data.windows.map((item) => ({ ...item, active: false }));
  f.respond(jsonHandler(data));
  await f.context.loadPacing();
  assert.equal(f.text("#pacing-now"), "拟人节奏已关闭：任务按最快速度执行");
  assert.equal(f.text("#pacing-flag"), "已关闭");
  assert.equal(f.el("#pacing-flag").dataset.state, "off");
  assert.equal(f.text("#pacing-state"), "节奏已关闭");
  assert.match(f.text("#pacing-note"), /不退出登录/);
});

test("a failed request surfaces an error and keeps the tab honest", async () => {
  const f = harness();
  f.respond(async () => ({ ok: false, status: 503, json: async () => ({}) }));
  await f.context.loadPacing();
  assert.equal(f.el("#pacing-error").hidden, false);
  assert.match(f.text("#pacing-error"), /读取拟人节奏状态失败/);
  assert.equal(f.text("#pacing-state"), "待连接");
});

test("the view refreshes while visible and stops when left", async () => {
  const f = harness();
  f.respond(jsonHandler(snapshot()));
  f.context.pacingViewChanged("pacing");
  assert.equal(f.requests.length, 1);
  assert.equal(f.requests[0].url, "/api/backend/pacing");
  assert.deepEqual([...f.intervals.values()].map((timer) => timer.delay).sort(), [1000, 30000]);
  f.context.pacingViewChanged("tasks");
  assert.equal(f.intervals.size, 0);
});
