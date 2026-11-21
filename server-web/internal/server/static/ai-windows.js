const aiWindows = {
  active: false, layout: "both", statusTimer: null, statusController: null,
  windows: new Map(["deepseek", "doubao"].map((id) => [id, {
    id, paused: false, busy: false, available: false, interactive: false,
    zoom: 1, timer: null, controller: null,
    frameURL: "", failures: 0, sequence: 0,
    input: null, inputQueue: Promise.resolve(), dragging: false,
    inputQuietUntil: 0, pendingText: "", textTimer: null,
  }])),
};

function aiWindowElement(id, name) { return document.querySelector(`#ai-${id}-${name}`); }
function aiWindowName(state) { return state.id === "doubao" ? "豆包" : "DeepSeek"; }
function aiWindowVisible(state) {
  const fullscreen = document.fullscreenElement?.id;
  return aiWindows.active && !document.hidden &&
    (aiWindows.layout === "both" || aiWindows.layout === state.id) &&
    (!fullscreen || fullscreen === `ai-window-${state.id}`);
}
function aiWindowState(state, text, tone) {
  const node = aiWindowElement(state.id, "state");
  node.textContent = text;
  node.dataset.state = tone;
}
function aiWindowCanInteract(state) {
  return aiWindows.active && !document.hidden && !state.paused && !state.busy &&
    state.available && state.failures === 0 &&
    document.fullscreenElement?.id === `ai-window-${state.id}`;
}
function updateAIWindowInteraction(state) {
  state.interactive = aiWindowCanInteract(state);
  const surface = document.querySelector(`#ai-window-${state.id} .ai-window-surface`);
  surface.dataset.interactive = String(state.interactive);
  surface.setAttribute("aria-label", state.interactive
    ? `${aiWindowName(state)} 全屏可操作画面` : `${aiWindowName(state)} 实时画面`);
  if (state.input) {
    state.input.tabIndex = state.interactive ? 0 : -1;
    if (!state.interactive) state.input.blur();
  }
  if (document.fullscreenElement?.id === `ai-window-${state.id}`) {
    aiWindowElement(state.id, "activity").textContent = !state.available ? "页面未就绪"
      : state.paused ? "画面已暂停" : state.busy ? "正在处理 · 操作暂不可用"
        : state.failures ? "连接中断 · 操作暂不可用" : "空闲 · 可直接操作";
  }
}
function aiWindowPoint(state, event) {
  const image = aiWindowElement(state.id, "frame");
  const width = image.naturalWidth;
  const height = image.naturalHeight;
  if (!width || !height) return null;
  const rect = image.getBoundingClientRect();
  const scale = Math.min(rect.width / width, rect.height / height);
  const contentWidth = width * scale;
  const contentHeight = height * scale;
  const left = rect.left + (rect.width - contentWidth) / 2;
  const top = rect.top + (rect.height - contentHeight) / 2;
  if (event.clientX < left || event.clientX > left + contentWidth ||
      event.clientY < top || event.clientY > top + contentHeight) return null;
  return {
    x: Math.max(0, Math.min(1, (event.clientX - left) / contentWidth)),
    y: Math.max(0, Math.min(1, (event.clientY - top) / contentHeight)),
  };
}
function queueAIWindowInput(state, payload) {
  if (!state.interactive) return;
  state.inputQuietUntil = performance.now() + 450;
  state.inputQueue = state.inputQueue.then(async () => {
    const response = await fetch(`/api/ai-windows/${state.id}/input`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      if (response.status === 409) state.busy = true;
      throw new Error(response.status === 409 ? "AI 正在处理" : "操作未送达");
    }
  }).catch((error) => {
    aiWindowElement(state.id, "message").textContent = error.message;
    updateAIWindowInteraction(state);
  });
}
function createAIWindowInput(state) {
  const surface = document.querySelector(`#ai-window-${state.id} .ai-window-surface`);
  const input = document.createElement("textarea");
  input.className = "ai-window-input";
  input.autocomplete = "off";
  input.spellcheck = false;
  input.tabIndex = -1;
  input.setAttribute("aria-label", `${aiWindowName(state)} 操作输入`);
  surface.append(input);
  state.input = input;
  let lastMove = 0;
  let moveTimer = null;
  let pendingMove = null;
  let pointerStart = null;
  let dragSent = false;
  const focusInput = () => {
    input.value = "";
    input.focus({ preventScroll: true });
  };
  const flushText = () => {
    const value = input.value;
    input.value = "";
    if (!value) return;
    state.pendingText += value;
    clearTimeout(state.textTimer);
    state.textTimer = setTimeout(() => {
      const text = state.pendingText;
      state.pendingText = "";
      if (text) queueAIWindowInput(state, { type: "text", text });
    }, 50);
  };
  const sendPoint = (type, point) => queueAIWindowInput(state, {
    type, x: point.x, y: point.y, button: "left",
  });

  surface.addEventListener("pointerdown", (event) => {
    if (!state.interactive || event.button !== 0) return;
    const point = aiWindowPoint(state, event);
    if (!point) return;
    event.preventDefault();
    focusInput();
    state.dragging = true;
    pointerStart = point;
    dragSent = false;
    surface.setPointerCapture?.(event.pointerId);
  });
  surface.addEventListener("pointermove", (event) => {
    if (!state.interactive || !state.dragging) return;
    const point = aiWindowPoint(state, event);
    if (!point) return;
    if (!dragSent) {
      if (Math.hypot(point.x - pointerStart.x, point.y - pointerStart.y) < 0.004) return;
      dragSent = true;
      sendPoint("mouseDown", pointerStart);
    }
    const now = performance.now();
    pendingMove = point;
    if (now - lastMove < 80) {
      if (!moveTimer) {
        moveTimer = setTimeout(() => {
          moveTimer = null;
          if (pendingMove && state.interactive && state.dragging) {
            lastMove = performance.now();
            sendPoint("mouseMove", pendingMove);
          }
        }, 80);
      }
      return;
    }
    lastMove = now;
    sendPoint("mouseMove", point);
  });
  const endPointer = (event) => {
    if (!state.dragging) return;
    const point = aiWindowPoint(state, event);
    state.dragging = false;
    pendingMove = null;
    clearTimeout(moveTimer);
    moveTimer = null;
    if (point) sendPoint(dragSent ? "mouseUp" : "click", point);
    pointerStart = null;
    dragSent = false;
    surface.releasePointerCapture?.(event.pointerId);
    focusInput();
  };
  surface.addEventListener("pointerup", endPointer);
  surface.addEventListener("pointercancel", endPointer);
  surface.addEventListener("wheel", (event) => {
    if (!state.interactive) return;
    const point = aiWindowPoint(state, event);
    if (!point) return;
    event.preventDefault();
    queueAIWindowInput(state, {
      type: "wheel", x: point.x, y: point.y,
      deltaX: Math.max(-10000, Math.min(10000, event.deltaX)),
      deltaY: Math.max(-10000, Math.min(10000, event.deltaY)),
    });
  }, { passive: false });
  input.addEventListener("input", (event) => {
    if (!event.isComposing) flushText();
  });
  input.addEventListener("compositionend", flushText);
  input.addEventListener("keydown", (event) => {
    if (!state.interactive || event.isComposing) return;
    const modifiers = {
      alt: event.altKey, control: event.ctrlKey, meta: event.metaKey, shift: event.shiftKey,
    };
    const special = ["Enter", "Tab", "Backspace", "Escape", "Delete", "ArrowUp", "ArrowDown",
      "ArrowLeft", "ArrowRight", "Home", "End", "PageUp", "PageDown"];
    const paste = (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "v";
    const command = event.key.length === 1
      ? (event.ctrlKey || event.metaKey || event.altKey) && !paste
      : special.includes(event.key) || event.key.length > 1;
    if (!command) return;
    event.preventDefault();
    queueAIWindowInput(state, { type: "key", key: event.key, ...modifiers });
  });
}
function stopAIWindow(state) {
  clearTimeout(state.timer);
  state.sequence++;
  state.controller?.abort();
  state.controller = null;
}
function scheduleAIWindow(state, delay) {
  clearTimeout(state.timer);
  if (aiWindowVisible(state) && !state.paused) {
    const quiet = Math.max(0, state.inputQuietUntil - performance.now());
    state.timer = setTimeout(() => void loadAIWindowFrame(state), Math.max(delay, quiet));
  }
}

async function loadAIWindowFrame(state) {
  if (!aiWindowVisible(state) || state.paused || state.controller) return;
  const sequence = ++state.sequence;
  const controller = new AbortController();
  state.controller = controller;
  const timeout = setTimeout(() => controller.abort(), 8500);
  let nextURL = "";
  try {
    const response = await fetch(`/api/ai-windows/${state.id}/frame`, {
      cache: "no-store", signal: controller.signal,
    });
    if (!response.ok || response.headers.get("Content-Type") !== "image/jpeg") {
      throw new Error("frame unavailable");
    }
    const blob = await response.blob();
    if (blob.size > 4 * 1024 * 1024) throw new Error("frame too large");
    if (sequence !== state.sequence) return;
    nextURL = URL.createObjectURL(blob);
    const decoded = new Image();
    decoded.src = nextURL;
    await decoded.decode();
    if (sequence !== state.sequence || !aiWindowVisible(state) || state.paused) return;
    const image = aiWindowElement(state.id, "frame");
    const previousURL = state.frameURL;
    image.src = nextURL;
    state.frameURL = nextURL;
    nextURL = "";
    if (previousURL) URL.revokeObjectURL(previousURL);
    image.hidden = false;
    aiWindowElement(state.id, "placeholder").hidden = true;
    state.failures = 0;
    state.available = true;
    aiWindowState(state, "已连接", "online");
    aiWindowElement(state.id, "message").textContent = "实时画面";
    const rawTime = Number(response.headers.get("X-Frame-Captured-At"));
    const captured = new Date(rawTime > 0 ? rawTime : Date.now());
    const time = aiWindowElement(state.id, "time");
    time.dateTime = captured.toISOString();
    time.textContent = captured.toLocaleTimeString("zh-CN", { hour12: false });
    updateAIWindowInteraction(state);
  } catch {
    if (sequence !== state.sequence) return;
    state.failures++;
    aiWindowState(state, "重连中", "degraded");
    aiWindowElement(state.id, "message").textContent = state.frameURL
      ? "连接中断 · 保留上次画面" : "画面暂不可用 · 正在自动重连";
    updateAIWindowInteraction(state);
  } finally {
    clearTimeout(timeout);
    if (nextURL) URL.revokeObjectURL(nextURL);
    if (sequence === state.sequence) {
      state.controller = null;
      scheduleAIWindow(state, state.failures
        ? Math.min(10000, 1000 * 2 ** state.failures)
        : document.fullscreenElement?.id === `ai-window-${state.id}` ? 1500
          : state.busy ? 1000 : 2000);
    }
  }
}

async function loadAIWindowsStatus() {
  if (!aiWindows.active || document.hidden || aiWindows.statusController) return;
  clearTimeout(aiWindows.statusTimer);
  const controller = new AbortController();
  aiWindows.statusController = controller;
  const timeout = setTimeout(() => controller.abort(), 8500);
  try {
    const response = await fetch("/api/ai-windows", { cache: "no-store", signal: controller.signal });
    if (!response.ok) throw new Error("status unavailable");
    const result = await response.json();
    if (aiWindows.statusController !== controller) return;
    for (const state of aiWindows.windows.values()) {
      const item = result.windows?.find((entry) => entry.id === state.id);
      state.available = Boolean(item?.available);
      state.busy = Boolean(item?.busy);
      updateAIWindowInteraction(state);
      if (document.fullscreenElement?.id !== `ai-window-${state.id}`) {
        aiWindowElement(state.id, "activity").textContent = !state.available
          ? "页面未就绪" : state.busy ? "正在处理" : "空闲";
      }
    }
    document.querySelector("#ai-windows-status").textContent = "服务器直连";
  } catch {
    if (aiWindows.statusController === controller) {
      document.querySelector("#ai-windows-status").textContent = "服务连接中断 · 自动重连中";
    }
  } finally {
    clearTimeout(timeout);
    if (aiWindows.statusController === controller) {
      aiWindows.statusController = null;
      if (aiWindows.active && !document.hidden) {
        aiWindows.statusTimer = setTimeout(() => void loadAIWindowsStatus(), 5000);
      }
    }
  }
}

function syncAIWindows() {
  clearTimeout(aiWindows.statusTimer);
  aiWindows.statusController?.abort();
  aiWindows.statusController = null;
  for (const state of aiWindows.windows.values()) {
    stopAIWindow(state);
    updateAIWindowInteraction(state);
    if (aiWindowVisible(state) && !state.paused) void loadAIWindowFrame(state);
  }
  if (aiWindows.active && !document.hidden) void loadAIWindowsStatus();
}
function aiWindowsViewChanged(view) {
  aiWindows.active = view === "ai-windows";
  syncAIWindows();
}

for (const state of aiWindows.windows.values()) {
  const { id } = state;
  createAIWindowInput(state);
  aiWindowElement(id, "pause").addEventListener("click", () => {
    state.paused = !state.paused;
    const button = aiWindowElement(id, "pause");
    button.title = state.paused ? "继续画面" : "暂停画面";
    button.setAttribute("aria-label", `${state.paused ? "继续" : "暂停"}${id === "doubao" ? "豆包" : "DeepSeek"}画面`);
    button.setAttribute("aria-pressed", String(state.paused));
    const icon = document.createElement("i");
    icon.setAttribute("data-lucide", state.paused ? "play" : "pause");
    button.replaceChildren(icon);
    consoleUIIcons();
    stopAIWindow(state);
    if (state.paused) {
      aiWindowState(state, "已暂停", "unknown");
      aiWindowElement(id, "message").textContent = "画面已暂停";
    } else void loadAIWindowFrame(state);
    updateAIWindowInteraction(state);
  });
  aiWindowElement(id, "refresh").addEventListener("click", () => {
    state.failures = 0;
    stopAIWindow(state);
    if (state.paused) aiWindowElement(id, "pause").click();
    else void loadAIWindowFrame(state);
    void loadAIWindowsStatus();
  });
  const zoom = (delta) => {
    state.zoom = Math.max(1, Math.min(3, Math.round((state.zoom + delta) * 10) / 10));
    document.querySelector(`#ai-window-${id} .ai-window-surface`).style.setProperty("--zoom", state.zoom);
    aiWindowElement(id, "zoom").textContent = `${Math.round(state.zoom * 100)}%`;
    aiWindowElement(id, "zoom-out").disabled = state.zoom <= 1;
    aiWindowElement(id, "zoom-in").disabled = state.zoom >= 3;
  };
  aiWindowElement(id, "zoom-in").addEventListener("click", () => zoom(0.2));
  aiWindowElement(id, "zoom-out").addEventListener("click", () => zoom(-0.2));
  aiWindowElement(id, "fullscreen").addEventListener("click", async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await document.querySelector(`#ai-window-${id}`).requestFullscreen();
    } catch {
      document.querySelector(`#ai-window-layout [data-layout="${id}"]`).click();
    }
  });
}
document.querySelectorAll("#ai-window-layout button").forEach((button) => {
  button.addEventListener("click", () => {
    aiWindows.layout = button.dataset.layout;
    document.querySelector("#ai-window-grid").dataset.layout = aiWindows.layout;
    for (const state of aiWindows.windows.values()) {
      document.querySelector(`#ai-window-${state.id}`).hidden =
        aiWindows.layout !== "both" && aiWindows.layout !== state.id;
    }
    document.querySelectorAll("#ai-window-layout button").forEach((item) => {
      item.setAttribute("aria-pressed", String(item === button));
    });
    syncAIWindows();
  });
});
document.addEventListener("visibilitychange", syncAIWindows);
document.addEventListener("fullscreenchange", () => {
  for (const state of aiWindows.windows.values()) {
    const button = aiWindowElement(state.id, "fullscreen");
    const active = document.fullscreenElement?.id === `ai-window-${state.id}`;
    button.title = active ? "退出全屏" : "全屏";
    button.setAttribute("aria-label", `${active ? "退出全屏" : "全屏显示"}${state.id === "doubao" ? "豆包" : "DeepSeek"}`);
    updateAIWindowInteraction(state);
  }
  syncAIWindows();
});
window.addEventListener("pagehide", () => {
  aiWindows.active = false;
  syncAIWindows();
  for (const state of aiWindows.windows.values()) {
    if (state.frameURL) URL.revokeObjectURL(state.frameURL);
    state.frameURL = "";
  }
});
window.addEventListener("pageshow", (event) => {
  if (event.persisted) {
    aiWindowsViewChanged(document.querySelector("#ai-windows-view").hidden ? "" : "ai-windows");
  }
});
