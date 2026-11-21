const insightElement = (id) => document.querySelector(`#${id}`);
const insightTypes = {
  single: "单选题", multiple: "多选题", judgement: "判断题",
  completion: "填空题", shortanswer: "简答题",
};
const insights = {
  connected: false, view: "overview", generation: 0, timer: null, searchTimer: null,
  resourcesAt: 0, statsAt: 0, listAt: 0, resourcesLoading: false, statsLoading: false,
  listLoading: false, listRequest: 0, detailRequest: 0, page: 1, pages: 1,
  rows: new Map(), courseSignature: "", errors: { stats: "", list: "" },
};

function insightText(id, value) {
  const element = insightElement(id);
  if (element.textContent !== String(value)) element.textContent = String(value);
}

function insightBytes(value) {
  if (!Number.isFinite(value) || value < 0) return "--";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

function insightCount(value) {
  return Number.isFinite(value) && value >= 0 ? value.toLocaleString("zh-CN") : "--";
}

function insightDate(value) {
  if (!Number.isFinite(value)) return "--";
  const date = new Date(value * 1000);
  return Number.isNaN(date.getTime()) ? "--" : date.toLocaleString("zh-CN", { hour12: false });
}

function insightError(id, message) {
  insightText(id, message);
  insightElement(id).hidden = !message;
}

function renderResourceMetric(name, sample, note) {
  const percent = sample?.percent;
  const available = Number.isFinite(percent) && percent >= 0 && percent <= 100;
  const text = available ? `${percent.toFixed(1)}%` : "--";
  insightText(`resource-${name}`, text);
  const ring = insightElement(`resource-${name}-bar`);
  ring.style.setProperty("--progress", available ? `${percent}%` : "0%");
  ring.setAttribute("data-state", available ? "ready" : "unknown");
  ring.setAttribute("data-level", available && percent >= 95 ? "critical" : available && percent >= 80 ? "warning" : "normal");
  ring.setAttribute("aria-valuetext", available ? text : "暂无数据");
  if (available) ring.setAttribute("aria-valuenow", String(percent));
  else ring.removeAttribute("aria-valuenow");
  insightText(`resource-${name}-note`, note);
}

function resourceDiskNote(disk) {
  return disk ? `已用 ${insightBytes(disk.used_bytes)} / ${insightBytes(disk.total_bytes)}` : "--";
}

function resourceDiskDetail(disk) {
  return disk ? [
    `可用 ${insightBytes(disk.free_bytes)}`,
    [disk.device, disk.mountpoint].filter(Boolean).join(" · "),
  ].filter(Boolean).join(" · ") : "--";
}

function renderResources(sample = {}) {
  renderResourceMetric("cpu", sample.cpu, sample.cpu?.logical_count
    ? `${sample.cpu.logical_count} 个逻辑核心` : "等待采样");
  renderResourceMetric("memory", sample.memory, sample.memory
    ? `${insightBytes(sample.memory.used_bytes)} / ${insightBytes(sample.memory.total_bytes)}` : "--");
  const disks = Array.isArray(sample.disks) ? sample.disks : [];
  const system = disks.find((disk) => disk.role === "system");
  const data = disks.find((disk) => disk.role === "data") || sample.disk;
  const shared = sample.data_disk_shared === true;
  renderResourceMetric("system", system, resourceDiskNote(system));
  renderResourceMetric("disk", shared ? null : data, shared ? "与系统盘共用" : resourceDiskNote(data));
  if (shared) {
    insightText("resource-disk", "共用");
    insightElement("resource-disk-bar").setAttribute("aria-valuetext", "与系统盘共用");
  }
  insightText("resource-system-detail", resourceDiskDetail(system));
  insightText("resource-disk-detail", shared ? "未使用独立数据盘" : resourceDiskDetail(data));
  insightText("resource-disk-label", !shared && disks.some((disk) => disk.role === "data") ? "数据盘" : "数据所在磁盘");
  insightText("resource-data-path", sample.data_directory || "--");
  const network = sample.network;
  insightText("resource-network", Number.isFinite(network?.received_bytes_per_second)
    ? `${insightBytes(network.received_bytes_per_second)}/s` : "--");
  insightText("resource-network-note", network
    ? `上行 ${insightBytes(network.sent_bytes_per_second)}/s` : "--");
  insightText("resource-network-total", network
    ? `累计 ↓ ${insightBytes(network.received_bytes)} / ↑ ${insightBytes(network.sent_bytes)}` : "--");
  const uptime = sample.uptime_seconds;
  insightText("resource-uptime", Number.isFinite(uptime)
    ? `${Math.floor(uptime / 86400)} 天 ${Math.floor(uptime % 86400 / 3600)} 时 ${Math.floor(uptime % 3600 / 60)} 分` : "--");
  insightText("resource-process", insightBytes(sample.backend_rss_bytes));
  insightText("resource-swap", sample.swap
    ? sample.swap.enabled
      ? `${insightBytes(sample.swap.used_bytes)} / ${insightBytes(sample.swap.total_bytes)}`
      : "未启用" : "--");
  insightText("resource-load", Array.isArray(sample.cpu?.load_average)
    ? sample.cpu.load_average.map((value) => value.toFixed(2)).join(" / ") : "--");
  insightText("resource-host", sample.platform
    ? `后端所在服务器 · ${sample.platform === "linux" ? "Linux" : sample.platform}` : "后端所在服务器");
}

function renderBankStats(stats = {}) {
  const storage = stats.storage || {};
  for (const prefix of ["bank", "overview-bank"]) {
    insightText(`${prefix}-entries`, insightCount(stats.entries));
    insightText(`${prefix}-hits`, insightCount(stats.hits));
    insightText(`${prefix}-content`, insightBytes(storage.content_bytes));
    insightText(`${prefix}-database`, insightBytes(storage.database_bytes));
  }
  insightText("bank-courses-count", Number.isFinite(stats.course_count) ? `${stats.course_count} 门课程` : "--");
  insightText("bank-shared", Number.isFinite(stats.shared_waits) ? `同题共享等待 ${insightCount(stats.shared_waits)} 次` : "--");
  insightText("bank-content-note", storage.question_bytes != null
    ? `题目 ${insightBytes(storage.question_bytes)} · 答案 ${insightBytes(storage.answer_bytes)}${
      storage.image_count ? ` · 图片 ${insightCount(storage.image_count)} 张 / ${insightBytes(storage.image_bytes)}` : ""
    }` : "题目与答案");
  insightText("bank-overview-note", Number.isFinite(stats.course_count)
    ? `${stats.course_count} 门课程 · 同题共享 ${insightCount(stats.shared_waits)} 次` : "等待题库统计");
  const courses = Array.isArray(stats.courses) ? stats.courses : [];
  const signature = JSON.stringify(courses);
  if (signature !== insights.courseSignature) {
    insights.courseSignature = signature;
    const select = insightElement("bank-course");
    const selected = select.value;
    const options = [{ id: "", title: "全部课程" }, ...courses];
    if (selected && !courses.some((course) => course.id === selected)) {
      options.push({ id: selected, title: `课程 ${selected}` });
    }
    select.replaceChildren(...options.map((course) => {
      const option = document.createElement("option");
      option.value = course.id;
      option.textContent = course.id
        ? `${course.title || `课程 ${course.id}`} (${insightCount(course.entries)})`
        : course.title;
      return option;
    }));
    select.value = selected;
  }
}

function updateBankError() {
  insightError("bank-error", insights.errors.stats || insights.errors.list);
}

async function loadResources(force = false) {
  if (!insights.connected || insights.resourcesLoading
    || (!force && insights.resourcesAt && Date.now() - insights.resourcesAt < 5000)) return;
  const generation = insights.generation;
  insights.resourcesLoading = true;
  insightElement("resource-refresh").disabled = true;
  try {
    const sample = await backendFetch("/resources");
    if (generation !== insights.generation) return;
    renderResources(sample);
    insightText("resource-status", `${sample.status === "partial" ? "部分指标不可用 · " : ""}${new Date(sample.sampled_at * 1000).toLocaleTimeString()}`);
    insightError("resource-error", "");
  } catch (error) {
    if (generation !== insights.generation) return;
    insightText("resource-status", "资源数据未更新");
    insightError("resource-error", error.message || "服务器资源读取失败");
  } finally {
    if (generation === insights.generation) {
      insights.resourcesAt = Date.now();
      insights.resourcesLoading = false;
      insightElement("resource-refresh").disabled = !insights.connected;
    }
  }
}

async function loadBankStats(force = false) {
  if (!insights.connected || insights.statsLoading
    || (!force && insights.statsAt && Date.now() - insights.statsAt < 5000)) return;
  const generation = insights.generation;
  insights.statsLoading = true;
  try {
    const stats = await backendFetch("/question-bank/stats");
    if (generation !== insights.generation) return;
    renderBankStats(stats);
    insights.errors.stats = "";
    insightText("bank-status", `最近同步 ${new Date(stats.sampled_at * 1000).toLocaleTimeString()}`);
  } catch (error) {
    if (generation !== insights.generation) return;
    insights.errors.stats = error.message || "题库统计读取失败";
    insightText("bank-status", "题库统计未更新");
    insightText("bank-overview-note", "题库统计未更新");
  } finally {
    if (generation === insights.generation) {
      insights.statsAt = Date.now();
      insights.statsLoading = false;
      updateBankError();
    }
  }
}

function bankQuery() {
  return new URLSearchParams({
    search: insightElement("bank-search").value.trim(),
    course_id: insightElement("bank-course").value,
    type: insightElement("bank-type").value,
    sort: insightElement("bank-sort").value,
    page: String(insights.page),
    page_size: insightElement("bank-page-size").value,
  }).toString();
}

function updateBankPagination() {
  insightElement("bank-prev").disabled = !insights.connected || insights.listLoading || insights.page <= 1;
  insightElement("bank-next").disabled = !insights.connected || insights.listLoading || insights.page >= insights.pages;
  insightElement("bank-refresh").disabled = !insights.connected || insights.listLoading || insights.statsLoading;
  insightText("bank-page", `第 ${insights.page} / ${Math.max(1, insights.pages)} 页`);
}

function createBankRow(id) {
  const item = document.createElement("li");
  item.className = "bank-row";
  const content = document.createElement("div");
  content.className = "bank-row-content";
  const meta = document.createElement("div");
  meta.className = "bank-row-meta";
  const type = document.createElement("span");
  type.className = "bank-type";
  const course = document.createElement("span");
  meta.append(type, course);
  const title = document.createElement("h3");
  const answer = document.createElement("p");
  answer.className = "bank-row-answer";
  const updated = document.createElement("span");
  updated.className = "muted";
  content.append(meta, title, answer, updated);
  const hits = document.createElement("div");
  hits.className = "bank-row-hits";
  const hitValue = document.createElement("strong");
  const hitLabel = document.createElement("span");
  hitLabel.textContent = "命中";
  hits.append(hitValue, hitLabel);
  const open = document.createElement("button");
  open.type = "button";
  open.className = "ghost icon-button";
  open.title = "查看题目详情";
  open.setAttribute("aria-label", "查看题目详情");
  const icon = document.createElement("i");
  icon.setAttribute("data-lucide", "arrow-up-right");
  open.append(icon);
  open.addEventListener("click", () => void openBankDetail(id));
  item.append(content, hits, open);
  return { item, type, course, title, answer, updated, hitValue };
}

function bankQuality(question) {
  if (question.historical) return "历史候选 · 不复用";
  if (question.validation === "confirmed") return "已确认";
  if (question.validation === "vision_cross_checked") return "原图复核 · 两路一致";
  if (question.image_count) return "仅格式检查 · 不复用";
  return "仅格式检查";
}

function renderBankList(result) {
  const keep = new Set();
  const children = result.items.map((question) => {
    keep.add(question.id);
    let row = insights.rows.get(question.id);
    if (!row) {
      row = createBankRow(question.id);
      insights.rows.set(question.id, row);
    }
    const values = {
      type: `${insightTypes[question.type] || question.type}${question.image_count ? ` · ${question.image_count} 图` : ""}`,
      course: question.course_title || `课程 ${question.course_id}`,
      title: question.title,
      answer: `已存答案：${question.answer_preview === "true" ? "正确" : question.answer_preview === "false" ? "错误" : question.answer_preview}`,
      updated: `${bankQuality(question)} · 更新 ${insightDate(question.updated_at)}`,
      hitValue: insightCount(question.hits),
    };
    for (const [key, value] of Object.entries(values)) {
      if (row[key].textContent !== String(value)) row[key].textContent = String(value);
    }
    return row.item;
  });
  syncChildren(insightElement("bank-list"), children);
  for (const id of insights.rows.keys()) if (!keep.has(id)) insights.rows.delete(id);
  insightElement("bank-empty").hidden = children.length > 0;
  insightText("bank-results", `共 ${insightCount(result.total)} 题`);
  insights.pages = result.pages;
  if (typeof consoleUIIcons === "function") consoleUIIcons();
}

async function loadBankList(force = false) {
  if (!insights.connected || insights.listLoading
    || (!force && insights.listAt && Date.now() - insights.listAt < 5000)) return;
  const generation = insights.generation;
  const request = ++insights.listRequest;
  const current = () => generation === insights.generation && request === insights.listRequest;
  insights.listLoading = true;
  insightElement("bank-list").setAttribute("aria-busy", "true");
  updateBankPagination();
  try {
    const result = await backendFetch(`/question-bank?${bankQuery()}`);
    if (!current()) return;
    // A shrinking bank can invalidate a saved page, but must not reset the filters.
    if (insights.page > Math.max(1, result.pages)) {
      insights.page = Math.max(1, result.pages);
      insights.listLoading = false;
      return loadBankList(true);
    }
    renderBankList(result);
    insights.errors.list = "";
  } catch (error) {
    if (!current()) return;
    insights.errors.list = error.message || "题目列表读取失败";
    insightText("bank-results", "列表未更新");
  } finally {
    if (current()) {
      insights.listAt = Date.now();
      insights.listLoading = false;
      insightElement("bank-list").setAttribute("aria-busy", "false");
      updateBankError();
      updateBankPagination();
    }
  }
}

function invalidateBankList() {
  insights.listRequest += 1;
  insights.listLoading = false;
  insights.listAt = 0;
}

function changeBankFilters(debounce = false) {
  insights.page = 1;
  invalidateBankList();
  clearTimeout(insights.searchTimer);
  if (debounce) insights.searchTimer = setTimeout(() => void loadBankList(true), 300);
  else void loadBankList(true);
}

function renderBankQuestionText(element, text, slots) {
  if (!/\[image_(?:[1-9]|10)\]/.test(text)) {
    element.textContent = text;
    return;
  }
  const parts = text.split(/(\[image_(?:[1-9]|10)\])/);
  element.replaceChildren(...parts.filter(Boolean).map((part) => {
    const imageID = /^\[(image_(?:[1-9]|10))\]$/.exec(part)?.[1];
    const span = document.createElement("span");
    span.textContent = imageID ? `${part} 正在读取` : part;
    if (imageID) {
      span.className = "bank-image";
      span.setAttribute("role", "status");
      const positions = slots.get(imageID) || [];
      positions.push(span);
      slots.set(imageID, positions);
    }
    return span;
  }));
}

async function loadBankQuestionImages(question, slots, current, resource = "question-bank", onOpen = null) {
  await Promise.allSettled([...slots].map(async ([imageID, positions]) => {
    try {
      const manifest = (question.images || []).find((image) => image.id === imageID);
      if (!manifest) throw new Error("图片清单缺失");
      const image = await backendFetch(`/${resource}/${encodeURIComponent(question.id)}/images/${imageID}`);
      if (!current()) return;
      if (image.id !== imageID || image.sha256 !== manifest.sha256
        || image.mime_type !== manifest.mime_type || image.byte_count !== manifest.byte_count
        || typeof image.data_url !== "string" || image.data_url.length > 2800000
        || !/^data:image\/(?:png|jpeg|webp|gif);base64,[A-Za-z0-9+/]+={0,2}$/.test(image.data_url)) {
        throw new Error("图片校验失败");
      }
      for (const position of positions) {
        const preview = document.createElement("img");
        preview.alt = `题目图片 ${imageID}`;
        preview.title = preview.alt;
        preview.decoding = "async";
        preview.addEventListener("error", () => {
          if (current()) position.textContent = `[${imageID}] 图片显示失败`;
        });
        preview.src = image.data_url;
        position.removeAttribute("role");
        if (onOpen) {
          const button = document.createElement("button");
          button.type = "button";
          button.className = "image-preview";
          button.title = `查看原图 ${imageID}`;
          button.setAttribute("aria-label", button.title);
          button.append(preview);
          button.addEventListener("click", () => onOpen(image));
          position.replaceChildren(button);
        } else {
          position.replaceChildren(preview);
        }
      }
    } catch {
      if (!current()) return;
      for (const position of positions) position.textContent = `[${imageID}] 图片读取失败`;
    }
  }));
}

async function openBankDetail(id) {
  if (!insights.connected) return;
  const dialog = insightElement("bank-detail");
  const generation = insights.generation;
  const request = ++insights.detailRequest;
  const current = () => generation === insights.generation && request === insights.detailRequest && dialog.open;
  insightText("bank-detail-heading", "题目详情");
  insightText("bank-detail-type", "共享题库");
  insightText("bank-detail-status", "正在读取");
  insightElement("bank-detail-body").hidden = true;
  insightError("bank-detail-error", "");
  if (!dialog.open) dialog.showModal();
  try {
    const question = await backendFetch(`/question-bank/${encodeURIComponent(id)}`);
    if (!current()) return;
    insightText("bank-detail-heading", question.course_title || `课程 ${question.course_id}`);
    insightText("bank-detail-type", insightTypes[question.type] || question.type);
    const slots = new Map();
    renderBankQuestionText(insightElement("bank-detail-title"), question.title, slots);
    const options = Array.isArray(question.options) ? question.options.join("\n") : question.options || "";
    insightElement("bank-options-section").hidden = !options;
    renderBankQuestionText(insightElement("bank-detail-options"), options, slots);
    insightElement("bank-detail-answers").replaceChildren(...question.answers.map((answer) => {
      const item = document.createElement("li");
      renderBankQuestionText(item, question.type === "judgement"
        ? answer === "true" ? "正确" : answer === "false" ? "错误" : answer : answer, slots);
      return item;
    }));
    insightText("bank-detail-validation", bankQuality(question));
    insightElement("bank-detail-meta").replaceChildren(...[
      ["课程 ID", question.course_id], ["题库命中", `${insightCount(question.hits)} 次`],
      ["同题共享等待", `${insightCount(question.shared_waits)} 次`],
      ["内容大小", insightBytes(question.content_bytes)], ["入库时间", insightDate(question.created_at)],
      ["更新时间", insightDate(question.updated_at)],
      ["答案检查", bankQuality(question)],
      ["题库复用", question.reusable === false ? "不复用" : "可复用"],
      ...(question.image_count ? [["题目图片", `${question.image_count} 张`]] : []),
    ].map(([label, value]) => {
      const row = document.createElement("div");
      const term = document.createElement("dt");
      const definition = document.createElement("dd");
      term.textContent = label;
      definition.textContent = value;
      row.append(term, definition);
      return row;
    }));
    insightText("bank-detail-status", "");
    insightElement("bank-detail-body").hidden = false;
    void loadBankQuestionImages(question, slots, current);
  } catch (error) {
    if (!current()) return;
    insightText("bank-detail-status", "详情未更新");
    insightError("bank-detail-error", error.message || "题目详情读取失败");
  }
}

function scheduleInsights() {
  clearTimeout(insights.timer);
  if (insights.connected && ["overview", "bank"].includes(insights.view)) {
    insights.timer = setTimeout(() => void refreshInsights(), document.hidden ? 20000 : 5000);
  }
}

async function refreshInsights(force = false) {
  clearTimeout(insights.timer);
  if (!insights.connected) return;
  const work = [];
  if (insights.view === "overview") work.push(loadResources(force), loadBankStats(force));
  if (insights.view === "bank") work.push(loadBankStats(force), loadBankList(force));
  await Promise.allSettled(work);
  updateBankPagination();
  scheduleInsights();
}

function insightsViewChanged(view) {
  insights.view = view;
  clearTimeout(insights.timer);
  if (["overview", "bank"].includes(view)) void refreshInsights();
}

function insightsConnectionChanged(connected, retain = false) {
  if (typeof imageReviewConnectionChanged === "function") imageReviewConnectionChanged(connected, retain);
  if (connected === insights.connected && (connected || retain)) return;
  insights.connected = connected;
  insights.generation += 1;
  insights.resourcesLoading = insights.statsLoading = false;
  insights.resourcesAt = insights.statsAt = 0;
  invalidateBankList();
  clearTimeout(insights.timer);
  clearTimeout(insights.searchTimer);
  insightElement("resource-refresh").disabled = !connected;
  insightElement("bank-list").setAttribute("aria-busy", "false");
  if (connected) {
    void refreshInsights(true);
  } else if (retain) {
    insightText("resource-status", "连接中断 · 保留上次数据");
    insightText("bank-status", "连接中断 · 保留上次数据");
  } else {
    insights.page = 1;
    insightElement("bank-course").value = "";
    renderResources();
    renderBankStats();
    insightText("resource-status", "等待连接");
    insightText("bank-status", "等待连接");
    insightText("bank-results", "尚未读取");
    insightElement("bank-list").replaceChildren();
    insightElement("bank-empty").hidden = true;
    insights.rows.clear();
    insights.errors = { stats: "", list: "" };
    insightError("resource-error", "");
    updateBankError();
    if (insightElement("bank-detail").open) insightElement("bank-detail").close();
  }
  updateBankPagination();
}

insightElement("overview-open-bank").addEventListener("click", () => consoleSelectView("bank"));
insightElement("resource-refresh").addEventListener("click", () => void loadResources(true));
insightElement("bank-refresh").addEventListener("click", () => void refreshInsights(true));
insightElement("bank-filters").addEventListener("submit", (event) => {
  event.preventDefault();
  changeBankFilters();
});
insightElement("bank-search").addEventListener("input", () => changeBankFilters(true));
for (const id of ["bank-course", "bank-type", "bank-sort", "bank-page-size"]) {
  insightElement(id).addEventListener("change", () => changeBankFilters());
}
insightElement("bank-clear").addEventListener("click", () => {
  insightElement("bank-search").value = insightElement("bank-course").value = insightElement("bank-type").value = "";
  insightElement("bank-sort").value = "updated";
  changeBankFilters();
});
for (const [id, delta] of [["bank-prev", -1], ["bank-next", 1]]) {
  insightElement(id).addEventListener("click", () => {
    if (insights.listLoading || insights.page + delta < 1 || insights.page + delta > insights.pages) return;
    insights.page += delta;
    invalidateBankList();
    void loadBankList(true);
  });
}
insightElement("bank-detail-close").addEventListener("click", () => insightElement("bank-detail").close());
insightElement("bank-detail").addEventListener("close", () => { insights.detailRequest += 1; });
document.addEventListener("visibilitychange", () => {
  if (document.hidden) scheduleInsights();
  else void refreshInsights();
});
