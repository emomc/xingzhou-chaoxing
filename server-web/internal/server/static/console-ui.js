function consoleUIIcons() {
  if (typeof lucide !== "undefined") lucide.createIcons({ attrs: { "aria-hidden": "true" } });
}

function consoleActionLabel(button, icon, label) {
  const graphic = document.createElement("i");
  graphic.setAttribute("data-lucide", icon);
  const text = document.createElement("span");
  text.textContent = label;
  button.replaceChildren(graphic, text);
  consoleUIIcons();
}

consoleUIIcons();
document.querySelector("#open-connection").addEventListener("click", () => {
  consoleSelectView("connection");
});

const consoleViews = {
  overview: { title: "系统总览", section: "workspace" },
  tasks: { title: "课程任务", section: "workspace" },
  archived: { title: "任务归档", section: "workspace" },
  accounts: { title: "账号管理", section: "workspace" },
  bank: { title: "共享题库", section: "workspace" },
  images: { title: "图片题核对", section: "workspace" },
  "ai-windows": { title: "AI 窗口", section: "workspace" },
  "ai-chat": { title: "AI 对话", section: "workspace" },
  notes: { title: "工作事项", section: "workspace" },
  pacing: { title: "拟人节奏", section: "workspace" },
  connection: { title: "连接设置", section: "service" },
  storage: { title: "数据存储", section: "service" },
  users: { title: "用户管理", section: "service" },
};
const consoleMobile = window.matchMedia("(max-width: 900px)");
let consoleSidebarReturnFocus = null;
let consoleCurrentView = "";

function setConsoleSidebar(open) {
  open = open && consoleMobile.matches;
  const sidebar = document.querySelector("#sidebar");
  const toggle = document.querySelector("#toggle-sidebar");
  const wasOpen = document.body.classList.contains("nav-open");
  if (open && !wasOpen) consoleSidebarReturnFocus = document.activeElement;
  document.body.classList.toggle("nav-open", open);
  toggle.setAttribute("aria-expanded", String(open));
  document.querySelector("#sidebar-mask").hidden = !open;
  sidebar.inert = consoleMobile.matches && !open;
  sidebar.setAttribute("aria-hidden", String(consoleMobile.matches && !open));
  document.querySelector("main").inert = open;
  const more = document.querySelector("#mobile-more");
  more.setAttribute("aria-expanded", String(open));
  more.setAttribute("aria-pressed", String(open));
  if (open && !wasOpen) {
    sidebar.querySelector('[aria-selected="true"]')?.focus();
  } else if (!open && wasOpen && consoleMobile.matches) {
    (consoleSidebarReturnFocus?.isConnected ? consoleSidebarReturnFocus : toggle).focus();
    consoleSidebarReturnFocus = null;
  }
}

function syncConsoleMobileNav(view) {
  for (const button of document.querySelectorAll("#mobile-nav button[data-view]")) {
    button.setAttribute("aria-pressed", String(button.dataset.view === view));
  }
}

function consoleSelectView(view) {
  const config = consoleViews[view];
  if (!config) return;
  const changed = consoleCurrentView !== view;
  consoleCurrentView = view;
  const workspace = config.section === "workspace";
  for (const name of Object.keys(consoleViews)) {
    document.querySelector(`#${name}-view`).hidden = name !== view;
    document.querySelector(`#${name}-tab`).setAttribute("aria-selected", String(name === view));
  }
  document.querySelector("#workspace-menu").hidden = !workspace;
  document.querySelector("#service-menu").hidden = workspace;
  document.querySelector("#workspace-nav").setAttribute("aria-pressed", String(workspace));
  document.querySelector("#service-nav").setAttribute("aria-pressed", String(!workspace));
  document.querySelector("#view-title").textContent = config.title;
  document.querySelector("#view-category").textContent = workspace ? "工作台" : "服务管理";
  document.querySelector("#sidebar-category").textContent = workspace ? "工作台" : "服务管理";
  syncConsoleMobileNav(view);
  document.querySelector("#dashboard-summary").hidden = view !== "overview";
  document.querySelector("#count").hidden = !["tasks", "archived", "notes", "accounts"].includes(view);
  const panel = document.querySelector("#connection-panel");
  document.querySelector(view === "connection" ? "#connection-settings-host" : "#connection-home").append(panel);
  if (view === "connection") panel.open = true;
  setConsoleSidebar(false);
  if (changed) window.scrollTo({ top: 0, left: 0, behavior: "auto" });
  if (typeof insightsViewChanged === "function") insightsViewChanged(view);
  if (typeof imageReviewViewChanged === "function") imageReviewViewChanged(view);
  if (typeof aiWindowsViewChanged === "function") aiWindowsViewChanged(view);
  if (typeof backendViewChanged === "function") backendViewChanged(view);
  if (typeof aiProviderViewChanged === "function") aiProviderViewChanged(view);
  if (typeof aiChatViewChanged === "function") aiChatViewChanged(view);
  if (typeof pacingViewChanged === "function") pacingViewChanged(view);
  if (typeof guardViewChanged === "function") guardViewChanged(view);
  if (typeof usersViewChanged === "function") usersViewChanged(view);
}

document.querySelector("#workspace-nav").addEventListener("click", () => document.querySelector("#overview-tab").click());
document.querySelector("#service-nav").addEventListener("click", () => consoleSelectView("connection"));
document.querySelector("#connection-tab").addEventListener("click", () => consoleSelectView("connection"));
document.querySelector("#storage-tab").addEventListener("click", () => consoleSelectView("storage"));
document.querySelector("#users-tab").addEventListener("click", () => consoleSelectView("users"));
document.querySelector("#bank-tab").addEventListener("click", () => consoleSelectView("bank"));
document.querySelector("#images-tab").addEventListener("click", () => consoleSelectView("images"));
document.querySelector("#ai-windows-tab").addEventListener("click", () => consoleSelectView("ai-windows"));
document.querySelector("#ai-chat-tab").addEventListener("click", () => consoleSelectView("ai-chat"));
document.querySelector("#pacing-tab").addEventListener("click", () => consoleSelectView("pacing"));
document.querySelector("#toggle-sidebar").addEventListener("click", () => {
  setConsoleSidebar(!document.body.classList.contains("nav-open"));
});
for (const button of document.querySelectorAll("#mobile-nav button[data-view]")) {
  button.addEventListener("click", () => consoleSelectView(button.dataset.view));
}
document.querySelector("#mobile-more").addEventListener("click", () => {
  setConsoleSidebar(!document.body.classList.contains("nav-open"));
});
document.querySelector("#sidebar-mask").addEventListener("click", () => setConsoleSidebar(false));
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") setConsoleSidebar(false);
  if (event.key !== "Tab" || !document.body.classList.contains("nav-open")) return;
  const controls = [document.querySelector("#toggle-sidebar"),
    ...document.querySelector("#sidebar").querySelectorAll("button")].filter((element) => element.getClientRects().length && !element.disabled);
  const index = controls.indexOf(document.activeElement);
  if ((event.shiftKey && index <= 0) || (!event.shiftKey && (index < 0 || index === controls.length - 1))) {
    event.preventDefault();
    controls[event.shiftKey ? controls.length - 1 : 0].focus();
  }
});
consoleMobile.addEventListener("change", () => setConsoleSidebar(false));
consoleSelectView("overview");

function renderConsoleStorage(prefix, storage = {}) {
  const text = (name, value) => { document.querySelector(`#storage-${prefix}-${name}`).textContent = value; };
  text("engine", storage.engine === "sqlite" ? "SQLite" : storage.engine === "json" ? "JSON" : "--");
  text("status", storage.status === "ok" ? "正常" : storage.engine ? "暂不可用" : "等待服务连接");
  text("journal", storage.journal_mode?.toUpperCase() || "--");
  text("size", storage.database_bytes == null ? "--" : `${(storage.database_bytes / 1048576).toFixed(2)} MB`);
  if (prefix === "console") text("version", storage.schema_version ?? "--");
  else {
    const records = storage.records;
    text("records", records ? `${records.accounts ?? 0} / ${records.jobs ?? 0} / ${records.answers ?? 0}` : "--");
  }
}

function updateConsoleBackendStorage(health = {}) {
  renderConsoleStorage("backend", health.storage);
}

let consoleStorageLoading = false;
async function loadConsoleStorage() {
  if (consoleStorageLoading) return;
  consoleStorageLoading = true;
  const button = document.querySelector("#refresh-storage");
  const error = document.querySelector("#storage-error");
  button.disabled = true;
  try {
    const response = await fetch("/api/health", { signal: AbortSignal.timeout(5000) });
    if (!response.ok) throw new Error("存储状态读取失败");
    const health = await response.json();
    const storage = health.storage || {};
    document.querySelector("#console-storage").textContent = storage.engine === "sqlite"
      ? "SQLite · WAL" : storage.engine === "json" ? "本机文件存储" : "存储状态未更新";
    renderConsoleStorage("console", storage);
    error.hidden = true;
  } catch {
    document.querySelector("#console-storage").textContent = "存储状态未更新";
    error.textContent = "控制台存储状态暂不可用";
    error.hidden = false;
  } finally {
    consoleStorageLoading = false;
    button.disabled = false;
  }
}
document.querySelector("#refresh-storage").addEventListener("click", () => {
  void loadConsoleStorage();
  if (typeof refreshBackend === "function") void refreshBackend();
});
loadConsoleStorage();
