const pacingState = { data: null, loading: false, loadingTimer: null, tickTimer: null, skew: 0 };

function pacingNode(id) {
  return document.querySelector(`#${id}`);
}

function pacingText(id, value) {
  const node = pacingNode(id);
  if (node) node.textContent = value;
}

function pacingSecondsLabel(low, high) {
  const format = (value) => (value < 60 ? `${Math.round(value)} 秒` : `${Math.round(value / 60)} 分钟`);
  if (low === high) return format(low);
  return `${format(low)}–${format(high)}`;
}

function pacingDuration(seconds) {
  const total = Math.max(0, Math.round(seconds));
  if (total < 60) return `${total} 秒`;
  const minutes = Math.round(total / 60);
  if (minutes < 60) return `${minutes} 分钟`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours} 小时 ${rest} 分钟` : `${hours} 小时`;
}

function pacingServerNow() {
  return Date.now() + pacingState.skew;
}

function pacingClockLabel(epochSeconds) {
  if (!epochSeconds) return "";
  const date = new Date(epochSeconds * 1000);
  return `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}

function pacingRow(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function renderPacingTimeline(data) {
  const track = pacingNode("pacing-track");
  if (!track) return;
  const children = [];
  for (const hour of [0, 6, 12, 18, 24]) {
    const tick = pacingRow("span", "pacing-tick", `${String(hour).padStart(2, "0")}:00`);
    tick.style.left = `${(hour / 24) * 100}%`;
    children.push(tick);
  }
  for (const window of data.windows || []) {
    const segments = window.wraps
      ? [[window.start_minute, 24 * 60], [0, window.end_minute]]
      : [[window.start_minute, window.end_minute]];
    for (const [from, to] of segments) {
      const band = pacingRow("span", "pacing-band", "");
      band.dataset.name = window.name;
      band.dataset.active = String(Boolean(window.active));
      band.title = `${window.label} ${window.start}–${window.end}`;
      band.style.left = `${(from / (24 * 60)) * 100}%`;
      band.style.width = `${((to - from) / (24 * 60)) * 100}%`;
      children.push(band);
    }
  }
  const clock = data.clock || {};
  if (typeof clock.minute_of_day === "number") {
    const marker = pacingRow("span", "pacing-now-line", "");
    marker.title = `当前 ${clock.time}`;
    marker.style.left = `${(clock.minute_of_day / (24 * 60)) * 100}%`;
    children.push(marker);
  }
  track.replaceChildren(...children);
}

function renderPacingWindows(data) {
  const list = pacingNode("pacing-windows");
  if (!list) return;
  const rows = (data.windows || []).map((window) => {
    const item = pacingRow("li", "pacing-window");
    item.dataset.name = window.name;
    item.dataset.active = String(Boolean(window.active));
    const name = pacingRow("span", "pacing-window-name", window.label);
    const time = pacingRow("span", "pacing-window-time", `${window.start}–${window.end}`);
    const meta = pacingRow(
      "span", "pacing-window-meta",
      `${Math.round(window.chance * 100)}% 概率 · 最长 ${window.cap_minutes} 分钟`,
    );
    const state = pacingRow(
      "span", "pacing-window-state",
      window.active ? "进行中" : `${pacingDuration(window.starts_in_minutes * 60)}后`,
    );
    item.replaceChildren(name, time, meta, state);
    return item;
  });
  list.replaceChildren(...rows);
}

function renderPacingGaps(data) {
  const body = pacingNode("pacing-gaps");
  if (!body) return;
  const labels = (data.labels || {}).gaps || {};
  const rows = Object.entries(data.gaps || {}).map(([key, range]) => {
    const item = pacingRow("tr", "");
    item.dataset.key = key;
    item.append(
      pacingRow("td", "", labels[key] || key),
      pacingRow("td", "pacing-range", pacingSecondsLabel(range[0], range[1])),
    );
    return item;
  });
  body.replaceChildren(...rows);
  const config = data.config || {};
  const groupSize = data.group_size || [2, 3];
  const notes = [`每 ${groupSize[0]}–${groupSize[1]} 门课一组，组后大休息`];
  notes.push(`${Math.round((data.micro_break_chance || 0) * 100)}% 概率插入一次短离开`);
  if (config.exit_on_long_pause) {
    notes.push(`超过 ${pacingDuration(config.exit_after || 300)}的大休息会退出登录、回来再登录`);
  } else {
    notes.push("大休息期间保持会话，不退出登录");
  }
  pacingText("pacing-note", notes.join("；"));
}

function pacingRemainingText(until) {
  if (!until) return "";
  const remaining = Math.round(until - pacingServerNow() / 1000);
  if (remaining <= 0) return `马上结束（预计 ${pacingClockLabel(until)}）`;
  return `还剩 ${pacingDuration(remaining)}（预计 ${pacingClockLabel(until)} 结束）`;
}

function renderPacingResting() {
  const list = pacingNode("pacing-resting");
  const empty = pacingNode("pacing-resting-empty");
  if (!list) return;
  const resting = [...((pacingState.data || {}).resting || [])];
  resting.sort((left, right) => (left.until || 0) - (right.until || 0));
  const rows = resting.map((item) => {
    const row = pacingRow("li", "pacing-rest");
    row.dataset.jobId = item.id;
    row.dataset.kind = item.kind || "";
    const head = pacingRow("div", "pacing-rest-head");
    head.append(
      pacingRow("strong", "", item.account || item.masked || "账号"),
      pacingRow("span", "pacing-rest-reason", item.reason || "节奏等待"),
    );
    const meta = pacingRow("div", "pacing-rest-meta");
    const course = item.course || "课程";
    const chapter = item.chapter ? ` · ${item.chapter}` : "";
    meta.textContent = `${course}${chapter}`;
    const countdown = pacingRow("span", "pacing-rest-countdown", pacingRemainingText(item.until));
    countdown.dataset.until = item.until || "";
    row.append(head, meta, countdown);
    return row;
  });
  list.replaceChildren(...rows);
  if (empty) empty.hidden = rows.length > 0;
}

function pacingTickResting() {
  const list = pacingNode("pacing-resting");
  if (!list) return;
  for (const countdown of list.querySelectorAll(".pacing-rest-countdown")) {
    const until = Number(countdown.dataset.until || 0);
    if (until) countdown.textContent = pacingRemainingText(until);
  }
}

function renderPacing(data) {
  const config = data.config || {};
  const clock = data.clock || {};
  const enabled = Boolean(config.enabled);
  pacingText("pacing-clock", clock.time || "--:--");
  pacingText("pacing-zone", clock.timezone ? `节奏时区 ${clock.timezone}` : "节奏时区");
  const flag = pacingNode("pacing-flag");
  if (flag) {
    flag.textContent = enabled ? "已开启" : "已关闭";
    flag.dataset.state = enabled ? "on" : "off";
  }
  const state = pacingNode("pacing-state");
  if (state) {
    state.dataset.state = enabled ? "ok" : "unknown";
    state.textContent = enabled ? "节奏已开启" : "节奏已关闭";
  }
  const windows = data.windows || [];
  const active = windows.find((window) => window.active);
  if (!enabled) {
    pacingText("pacing-now", "拟人节奏已关闭：任务按最快速度执行");
    pacingText("pacing-next", "在任务的“拟人节奏”选项重新打开后生效");
  } else if (active) {
    pacingText(
      "pacing-now",
      `现在是${active.label}时间（${active.start}–${active.end}）`,
    );
    pacingText(
      "pacing-next",
      `${Math.round(active.chance * 100)}% 的任务会停下来休息，最长 ${active.cap_minutes} 分钟`,
    );
  } else {
    pacingText("pacing-now", "现在不在休息时段，任务按随机间隔继续");
    const upcoming = windows
      .slice()
      .sort((left, right) => left.starts_in_minutes - right.starts_in_minutes)[0];
    pacingText(
      "pacing-next",
      upcoming
        ? `下一段：${upcoming.label} ${upcoming.start}，还有 ${pacingDuration(upcoming.starts_in_minutes * 60)}`
        : "没有配置休息时段",
    );
  }
  renderPacingTimeline(data);
  renderPacingWindows(data);
  renderPacingGaps(data);
  renderPacingResting();
}

function pacingStopTimers() {
  if (pacingState.loadingTimer) clearInterval(pacingState.loadingTimer);
  if (pacingState.tickTimer) clearInterval(pacingState.tickTimer);
  pacingState.loadingTimer = null;
  pacingState.tickTimer = null;
}

async function loadPacing() {
  if (pacingState.loading) return;
  pacingState.loading = true;
  const button = pacingNode("pacing-refresh");
  const error = pacingNode("pacing-error");
  if (button) button.disabled = true;
  try {
    const response = await fetch("/api/backend/pacing", { signal: AbortSignal.timeout(10000) });
    if (!response.ok) throw new Error("拟人节奏状态读取失败");
    const payload = await response.json();
    // 后端返回 { pacing: {...}, resting: [...] }
    const data = { ...(payload.pacing || {}), resting: payload.resting || [] };
    const clock = data.clock || {};
    if (typeof clock.now === "number") pacingState.skew = clock.now * 1000 - Date.now();
    pacingState.data = data;
    renderPacing(data);
    if (error) error.hidden = true;
  } catch {
    const state = pacingNode("pacing-state");
    if (state) {
      state.dataset.state = "unknown";
      state.textContent = "待连接";
    }
    if (error) {
      error.textContent = "读取拟人节奏状态失败，请确认后端已连接";
      error.hidden = false;
    }
  } finally {
    pacingState.loading = false;
    if (button) button.disabled = false;
  }
}

function pacingViewChanged(view) {
  if (view !== "pacing") {
    pacingStopTimers();
    return;
  }
  void loadPacing();
  pacingStopTimers();
  pacingState.loadingTimer = setInterval(() => void loadPacing(), 30000);
  pacingState.tickTimer = setInterval(pacingTickResting, 1000);
}

pacingNode("pacing-refresh")?.addEventListener("click", () => void loadPacing());
