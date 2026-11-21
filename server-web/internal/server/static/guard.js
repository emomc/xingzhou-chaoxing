const guardState = { data: null, loading: false, saving: false };

function guardNode(id) {
  return document.querySelector(`#${id}`);
}

function guardText(id, value) {
  const node = guardNode(id);
  if (node) node.textContent = value;
}

function guardSetState(state, text) {
  const node = guardNode("guard-state");
  if (!node) return;
  node.dataset.state = state;
  node.textContent = text;
}

function guardError(message) {
  const node = guardNode("guard-error");
  if (!node) return;
  node.textContent = message || "";
  node.hidden = !message;
}

function guardHint(message, state = "") {
  const node = guardNode("guard-hint");
  if (!node) return;
  node.textContent = message || "";
  node.dataset.state = state;
}

function guardSecondsLabel(seconds) {
  const value = Math.max(0, Math.round(Number(seconds) || 0));
  if (value < 60) return `${value} 秒`;
  const minutes = Math.floor(value / 60);
  const rest = value % 60;
  return rest ? `${minutes} 分 ${rest} 秒` : `${minutes} 分`;
}

function guardTimeLabel(epochSeconds) {
  if (!epochSeconds) return "";
  const date = new Date(epochSeconds * 1000);
  const pad = (value) => String(value).padStart(2, "0");
  return `${date.getMonth() + 1}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function guardSpeedText(value) {
  const speed = Number(value);
  if (!Number.isFinite(speed)) return "0.95";
  return Number.isInteger(speed) ? speed.toFixed(1) : String(Math.round(speed * 100) / 100);
}

function renderGuardRollbacks(rollbacks) {
  const list = guardNode("guard-rollback-list");
  const empty = guardNode("guard-rollback-empty");
  if (!list || !empty) return;
  const items = (rollbacks && rollbacks.items) || [];
  list.replaceChildren();
  empty.hidden = items.length > 0;
  for (const item of items) {
    const row = document.createElement("li");
    row.dataset.action = item.action || "slow";
    const title = document.createElement("span");
    title.className = "guard-rollback-title";
    title.textContent = [item.course, item.name].filter(Boolean).join(" · ") || item.job_id;
    const detail = document.createElement("span");
    detail.className = "guard-rollback-detail";
    const actionLabel = item.action === "pause" ? "已暂停后重看" : "已降到 1 倍速";
    detail.textContent = `${guardSecondsLabel(item.from_position)} → ${guardSecondsLabel(item.to_position)}（少 ${guardSecondsLabel(item.dropped)}） · ${actionLabel} · ${guardTimeLabel(item.created_at)}`;
    row.append(title, detail);
    list.append(row);
  }
}

function renderVideoGuard(data) {
  guardState.data = data;
  const maxSpeed = guardNode("guard-max-speed");
  const quietHours = guardNode("guard-quiet-hours");
  const autoPause = guardNode("guard-rollback-pause");
  const recheck = guardNode("guard-recheck");
  if (maxSpeed) maxSpeed.value = guardSpeedText(data.max_speed ?? 0.95);
  if (quietHours) quietHours.checked = Boolean(data.quiet_hours);
  if (autoPause) autoPause.checked = Boolean(data.auto_pause);
  if (recheck) recheck.value = String(data.recheck ?? 8);
  const rollbacks = data.rollbacks || { count: 0, items: [] };
  guardSetState("ok", rollbacks.count ? `近 7 天回退 ${rollbacks.count} 次` : "近 7 天无回退");
  const speedLabel = data.max_speed >= 1.5
    ? "1.5 倍速（上限）"
    : `${guardSpeedText(data.max_speed)} 倍速${Number(data.max_speed) < 1 ? "（慢放）" : ""}`;
  guardText(
    "guard-note",
    `当前最多 ${speedLabel}${data.quiet_hours ? " · 北京时间 00:00–06:00 不新开视频" : ""}${data.auto_pause ? " · 回退后自动暂停 15–30 分钟" : " · 回退只提醒并降到 1 倍速"}`,
  );
  renderGuardRollbacks(rollbacks);
}

async function loadVideoGuard() {
  if (guardState.loading) return;
  guardState.loading = true;
  const button = guardNode("guard-refresh");
  if (button) button.disabled = true;
  try {
    const response = await fetch("/api/backend/video/guard", { signal: AbortSignal.timeout(10000) });
    if (!response.ok) throw new Error("guard read failed");
    renderVideoGuard(await response.json());
    guardError("");
  } catch {
    guardSetState("unknown", "待连接");
    guardError("读取看护设置失败，请确认后端已连接");
  } finally {
    guardState.loading = false;
    if (button) button.disabled = false;
  }
}

async function saveVideoGuard() {
  if (guardState.saving) return;
  const maxSpeed = Number(guardNode("guard-max-speed")?.value);
  const recheck = Number(guardNode("guard-recheck")?.value);
  if (!(maxSpeed >= 0.5 && maxSpeed <= 1.5)) {
    guardError("倍速上限要在 0.5 到 1.5 之间");
    return;
  }
  if (!(Number.isInteger(recheck) && recheck >= 1 && recheck <= 50)) {
    guardError("回读间隔要是 1 到 50 之间的整数");
    return;
  }
  guardState.saving = true;
  const button = guardNode("guard-save");
  if (button) button.disabled = true;
  try {
    const response = await fetch("/api/backend/video/guard", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        max_speed: maxSpeed,
        quiet_hours: Boolean(guardNode("guard-quiet-hours")?.checked),
        auto_pause: Boolean(guardNode("guard-rollback-pause")?.checked),
        recheck,
      }),
      signal: AbortSignal.timeout(10000),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || "guard save failed");
    guardHint("已保存，下一次开始视频时生效", "ok");
    guardError("");
    void loadVideoGuard();
  } catch (error) {
    guardHint("");
    guardError(error.message === "guard save failed" ? "保存看护设置失败" : error.message);
  } finally {
    guardState.saving = false;
    if (button) button.disabled = false;
  }
}

function guardViewChanged(view) {
  if (view !== "pacing") return;
  void loadVideoGuard();
}

guardNode("guard-save")?.addEventListener("click", () => void saveVideoGuard());
guardNode("guard-refresh")?.addEventListener("click", () => void loadVideoGuard());
