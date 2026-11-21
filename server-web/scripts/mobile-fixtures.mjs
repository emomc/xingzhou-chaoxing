/* 手机端 UI 检查共用的假后端。
 *
 * Serves internal/server/static/ plus a stubbed /api layer, so the console can be rendered
 * in a real browser with realistic Chinese content (jobs in every state, a pacing wait, an
 * account list, AI conversations, pacing snapshot) without touching production or a database.
 *
 * startConsoleStub() resolves to { origin, requests, close }.
 */
import fs from "node:fs/promises";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const staticDir = fileURLToPath(new URL("../internal/server/static/", import.meta.url));
export const VIEWS = ["overview", "tasks", "archived", "accounts", "bank", "images", "ai-windows",
  "ai-chat", "notes", "pacing", "connection", "storage", "users"];

const NOW = Math.floor(Date.now() / 1000);

function job(id, status, label, progress, createdOffset, extra = {}) {
  return {
    id, account_id: "acc-" + id.slice(-1), label, status, course_ids: ["101", "202"],
    options: { videos_only: true, submit: false, preview: false, human_pacing: true },
    progress, created_at: NOW - createdOffset, updated_at: NOW - createdOffset + 5,
    started_at: NOW - createdOffset, finished_at: null, archived_at: null,
    worker_pid: 4242, error_code: "", outcome: "", ...extra,
  };
}

export const jobs = [
  job("job-aaaaaaa1", "running", "18100000001",
    { phase: "video", course: "军事理论（同济大学版）", chapter: "4.2 中国武装力量构成与国防动员体系",
      name: "4.2.1 中国武装力量构成.mp4", position: 120, duration: 600, completed: 3, total: 12,
      video_state: "playing" }, 600),
  job("job-aaaaaaa2", "running", "18200000002",
    { phase: "pacing", course: "国韵华章：中华传统戏曲鉴赏", chapter: "1.15 牢记中国国防耻辱【上】",
      name: "午饭时间，模拟休息，约 50 分钟", pacing_kind: "window", pacing_minutes: 50,
      pacing_until: NOW + 3000, completed: 5, total: 12 }, 900),
  job("job-aaaaaaa3", "running", "18300000003",
    { phase: "workid", course: "大学生心理健康教育", chapter: "2.1 情绪管理",
      ai_state: "queued", ai_type: "单选题", ai_question: "下列哪一项不是情绪的基本类型？",
      completed: 1, total: 9 }, 300),
  job("job-aaaaaaa4", "paused", "18400000004",
    { phase: "chapter", course: "大学生心理健康教育", chapter: "3.2 压力应对" }, 1500),
  job("job-aaaaaaa5", "needs_attention", "18500000005",
    { phase: "workid", course: "军事理论（同济大学版）", chapter: "5.1 信息化战争",
      ai_state: "error", ai_error: "需要人工核对答案", manual_tasks: [
        { type: "workid", title: "5.1.2 单元测验（共 20 题）", card_num: "3" },
      ] }, 2400, { error_code: "INCOMPLETE_TASKS", outcome: "needs_attention" }),
  job("job-aaaaaaa6", "succeeded", "18600000006",
    { phase: "course_confirmed", course: "国韵华章：中华传统戏曲鉴赏", chapter: "全部章节",
      completed: 12, total: 12 }, 3600),
];

const accounts = [
  { id: "acc-1", label: "18100000001", masked: "181****0001", username: "18100000001",
    state: "studying", courses: 14, jobs: 2, updated_at: NOW - 120, created_at: NOW - 86400 },
  { id: "acc-2", label: "18200000002", masked: "182****0002", username: "18200000002",
    state: "queued", courses: 9, jobs: 1, updated_at: NOW - 400, created_at: NOW - 86400 },
  { id: "acc-3", label: "山东省应用科技学院继续教育", masked: "186****0003",
    username: "18600000003", state: "attention", courses: 3, jobs: 1, updated_at: NOW - 900,
    created_at: NOW - 86400 },
];

const conversations = [{
  id: 3, created_at: NOW - 60, job_id: "job-aaaaaaa3", channel: "api", protocol: "responses",
  model: "minimax/minimax-m3.1-flash:free", route: "text", stage: "", prompt: "QUESTION_DATA: 2 + 2",
  response: '{"Answer":["B"]}', prompt_chars: 718, response_chars: 21, images: 0, status: "ok",
  error: "", elapsed_ms: 1840, truncated: 0,
}];

const pacing = {
  clock: { now: NOW, time: "12:10", date: "2026-10-22", minute_of_day: 730, timezone: "Asia/Shanghai" },
  config: { enabled: true, timezone: "Asia/Shanghai", exit_on_long_pause: true, exit_after: 300,
    exit_kinds: ["group", "window"], session_hooks: true },
  windows: [
    { name: "lunch", label: "午饭", start: "11:40", end: "13:00", start_minute: 700, end_minute: 780,
      wraps: false, chance: 0.7, cap_minutes: 60, active: true, starts_in_minutes: 0 },
    { name: "dinner", label: "晚饭", start: "17:30", end: "19:00", start_minute: 1050,
      end_minute: 1140, wraps: false, chance: 0.7, cap_minutes: 60, active: false, starts_in_minutes: 320 },
    { name: "night", label: "夜间休息", start: "23:30", end: "07:00", start_minute: 1410,
      end_minute: 420, wraps: true, chance: 1, cap_minutes: 90, active: false, starts_in_minutes: 680 },
  ],
  gaps: { answer: [1, 15], job: [2, 18], chapter: [8, 45], micro: [60, 300], course: [120, 600],
    group: [600, 1800], start: [10, 120] },
  labels: { windows: { lunch: "午饭", dinner: "晚饭", night: "夜间休息" },
    gaps: { answer: "看题思考", job: "切换任务点", chapter: "翻到下一节", micro: "离开一下",
      course: "换一门课", group: "这组刷完了歇一会", start: "开始学习" } },
  group_size: [2, 3], micro_break_chance: 0.05,
};
const resting = [
  { id: "job-aaaaaaa2", account_id: "acc-2", account: "18200000002", masked: "182****0002",
    course: "国韵华章：中华传统戏曲鉴赏", chapter: "1.15 牢记中国国防耻辱【上】",
    reason: "午饭时间，模拟休息，约 1 小时（已按上限截断）", kind: "window",
    until: NOW + 2400, minutes: 60, updated_at: NOW },
];

export async function startConsoleStub() {
  const requests = [];
  const server = http.createServer(async (req, res) => {
    try {
      const url = new URL(req.url, "http://127.0.0.1");
      const send = (status, data) => {
        res.writeHead(status, { "Content-Type": "application/json; charset=utf-8" });
        res.end(JSON.stringify(data));
      };
      for await (const _ of req) { /* drain */ }
      requests.push(url.pathname + url.search);

      if (url.pathname === "/api/session") {
        return send(200, { user: { id: "owner", username: "owner", role: "owner", credits: 0 } });
      }
      if (url.pathname === "/api/health") return send(200, { status: "ok", storage: { engine: "sqlite" } });
      if (url.pathname === "/api/items") {
        return send(200, { items: [
          { id: 1, title: "核对 5.1 单元测验的答案", detail: "第 3 张任务卡有 20 题需要人工确认，题库里没有命中。",
            status: "todo", updated_at: NOW - 600 },
          { id: 2, title: "给账号 182****0002 补一门课", detail: "", status: "doing", updated_at: NOW - 300 },
        ] });
      }
      if (url.pathname === "/api/ai-windows") {
        return send(200, { windows: [
          { id: "w1", title: "豆包 · 主窗口", state: "ready", url: "https://www.doubao.com/chat/",
            note: "已登录" },
        ] });
      }
      if (url.pathname === "/api/backend") return send(200, { configured: true, url: "http://127.0.0.1:3100" });
      if (url.pathname === "/api/users") {
        return send(200, { users: [
          { id: "owner", username: "owner", role: "owner", credits: 0, created_at: NOW - 86400 },
          { id: "u2", username: "operator", role: "user", credits: 12, created_at: NOW - 3600 },
        ] });
      }
      const isBackend = url.pathname.startsWith("/api/backend/");
      const api = isBackend ? url.pathname.slice("/api/backend".length) : "";
      if (api === "/health") {
        return send(200, {
          status: "ok", max_workers: 30, jobs: { running: 3, queued: 0, paused: 1 }, answers: {},
          storage: { engine: "sqlite", bytes: 81234567 }, ai_channel: "api", ai_api_configured: true,
          ai_vision_channel: "web", ai_vision_channel_explicit: true, ai_vision_enabled: true,
          ai_vision_fallback: true, ai_vision_direct: true,
          ai_vision_api_configured: true, ai_vision_api_separate: true,
          ai_vision_api_model: "nvidia/nemotron-3.5-super-vl:free",
          ai_api_model: "minimax/minimax-m3.1-flash:free", ai_api_vision_model: "",
          ai_api_protocol: "responses", ai_api_source: "console", ai_workers_alive: 1, ai_workers: 1,
          ai_conversations: 12, uptime: 3600,
        });
      }
      if (api === "/accounts") return send(200, { accounts });
      if (api === "/jobs") {
        return send(200, { jobs: url.searchParams.get("archived") === "1" ? [jobs[5]] : jobs });
      }
      if (/^\/jobs\/[^/]+\/events/.test(api)) return send(200, { events: [] });
      if (api.startsWith("/resources")) {
        return send(200, { status: "ok", platform: "linux", python: "3.12.3", disk: { total: 1e11, free: 4e10 },
          storage: { engine: "sqlite", path: "/var/lib/chaoxing-backend" }, ai: { proxy: "mihomo" } });
      }
      if (api.startsWith("/question-bank/stats")) {
        return send(200, { entries: 128, courses: [{ course: "军事理论", entries: 64 }], storage: { bytes: 40960 } });
      }
      if (api.startsWith("/question-bank")) {
        return send(200, { entries: [
          { id: "q1", question: "下列哪一项不是情绪的基本类型？", answer: "D", course: "大学生心理健康教育",
            hits: 12, updated_at: NOW - 900 },
        ], total: 1, page: 1, page_size: 20 });
      }
      if (api.startsWith("/image-questions")) {
        return send(200, { questions: [
          { id: "i1", question: "如图所示，该题答案为？", course: "军事理论", status: "review",
            images: 1, updated_at: NOW - 300 },
        ], total: 1, page: 1, page_size: 20 });
      }
      if (api === "/ai/provider") {
        return send(200, { configured: true, editable: true, source: "console",
          endpoint: "https://openrouter.ai/api/v1", protocol: "responses",
          model: "minimax/minimax-m3.1-flash:free", model_fallbacks: [], vision_model: "",
          timeout: 180, has_key: true, key_source: "console", proxy: "http://127.0.0.1:7890", stream: false });
      }
      if (api === "/ai/vision-provider") {
        return send(200, { configured: true, editable: true, inherited: false, source: "console",
          endpoint: "https://openrouter.ai/api/v1", protocol: "responses",
          model: "nvidia/nemotron-3.5-super-vl:free", model_fallbacks: [], vision_model: "",
          timeout: 180, has_key: true, key_source: "console", proxy: "http://127.0.0.1:7890", stream: false });
      }
      if (api === "/ai/channel") return send(200, { channel: "api", models: ["text-model"] });
      if (api.startsWith("/ai/conversations")) {
        return send(200, { conversations, latest: 3, retention: 500 });
      }
      if (api === "/pacing") return send(200, { pacing, resting });
      if (api === "/video/guard") {
        return send(200, {
          max_speed: 1.5, quiet_hours: true, auto_pause: false, recheck: 8,
          rollbacks: { count: 1, items: [{
            job_id: "job-aaaaaaa1", created_at: NOW - 600, course: "军事理论",
            name: "1.2 中国国防的历史", from_position: 620, to_position: 0, dropped: 620,
            action: "slow",
          }] },
        });
      }
      if (url.pathname === "/favicon.ico") { res.writeHead(204); res.end(); return; }
      if (url.pathname.startsWith("/api/")) return send(200, {});
      const name = url.pathname === "/" ? "index.html" : url.pathname.slice(1);
      const content = await fs.readFile(path.join(staticDir, name));
      const types = { ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".png": "image/png" };
      res.writeHead(200, { "Content-Type": types[path.extname(name)] || "application/octet-stream" });
      res.end(content);
    } catch (error) {
      res.writeHead(500, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ error: error.message }));
    }
  });
  server.listen(0, "127.0.0.1");
  await new Promise((resolve) => server.once("listening", resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  return { origin, requests, close: () => new Promise((resolve) => server.close(resolve)) };
}

export function launchBrowser(chromium) {
  return chromium.launch({
    headless: true,
    ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
      ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE } : {}),
  });
}
