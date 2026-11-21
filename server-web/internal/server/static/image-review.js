const imageReview = {
  connected: false, view: "overview", generation: 0, request: 0, detailRequest: 0,
  loading: false, detailLoading: false, timer: null, searchTimer: null,
  page: 1, pages: 1, rows: new Map(), selected: "", detailVersion: "",
  courseSignature: "", zoom: 100,
};
const imageStages = {
  recognizing: "豆包识别原图", solving: "DeepSeek 求解", verifying: "豆包独立复核",
  direct: "图片 API 一次直答",
  done: "结果已生成", needs_review: "等待人工核对", error: "处理失败",
};
const imageFailures = {
  AI_VISION_UNREADABLE: "原图中有无法可靠识别的内容，未采纳答案。",
  AI_VISION_READING_MISMATCH: "识别内容与原图不一致，未采纳答案。",
  AI_VISION_DISAGREEMENT: "DeepSeek 与豆包独立求解的答案不一致，未采纳答案。",
  AI_VISION_STAGE_FAILED: "图片处理阶段失败，详见下方处理记录。",
  AI_VISION_UNAVAILABLE: "图片模型暂不可用。",
  AI_UNAVAILABLE: "AI 服务暂不可用。",
  AI_RATE_LIMITED: "AI 上游限流，本次未采纳或提交答案，请等待恢复后重试。",
  AI_QUEUE_TIMEOUT: "AI 排队超时。",
  NO_ACTIVE_WAITERS: "关联任务已停止等待，未继续生成。",
  AI_UPGRADE_RETRY: "升级前的处理已中止，等待任务重新请求。",
};

function imageQuestionState(question) {
  if (question.state === "queued") return "排队中";
  if (question.state === "running") return imageStages[question.stage] || "AI 处理中";
  if (question.state === "error") return "处理失败 · 待核对";
  return question.needs_review ? "历史候选 · 待核对" : "已生成结果";
}

function imageQuestionVersion(question) {
  return JSON.stringify([question.id, question.state, question.stage, question.updated_at]);
}

function imageQuality(question) {
  if (question.validation === "confirmed") return "已确认";
  if (question.validation === "vision_cross_checked") return "两路一致 · 未人工确认";
  if (question.historical) return "历史候选 · 不复用";
  return question.state === "done" ? "仅格式检查 · 待核对" : "尚无已采纳答案";
}

function imageReviewControls() {
  insightElement("images-refresh").disabled = !imageReview.connected || imageReview.loading;
  insightElement("images-prev").disabled = !imageReview.connected || imageReview.loading || imageReview.page <= 1;
  insightElement("images-next").disabled = !imageReview.connected || imageReview.loading || imageReview.page >= imageReview.pages;
  insightText("images-page", `第 ${imageReview.page} / ${Math.max(1, imageReview.pages)} 页`);
  insightElement("images-list").setAttribute("aria-busy", String(imageReview.loading));
}

function createImageReviewRow(id) {
  const item = document.createElement("li");
  const button = document.createElement("button");
  button.type = "button";
  button.className = "image-review-row";
  const meta = document.createElement("span");
  meta.className = "image-row-meta";
  const type = document.createElement("span");
  const state = document.createElement("span");
  state.className = "image-state";
  meta.append(type, state);
  const title = document.createElement("strong");
  const course = document.createElement("span");
  course.className = "muted";
  const answer = document.createElement("span");
  answer.className = "image-row-answer";
  button.append(meta, title, course, answer);
  button.addEventListener("click", () => {
    selectImageQuestion(id);
    if (window.matchMedia("(max-width: 1100px)").matches) {
      insightElement("images-detail").scrollIntoView({ behavior: "smooth", block: "start" });
    }
  });
  item.append(button);
  return { item, button, type, state, title, course, answer, question: null };
}

function renderImageReviewList(result) {
  const keep = new Set();
  const children = result.items.map((question) => {
    keep.add(question.id);
    let row = imageReview.rows.get(question.id);
    if (!row) {
      row = createImageReviewRow(question.id);
      imageReview.rows.set(question.id, row);
    }
    row.question = question;
    const values = {
      type: `${insightTypes[question.type] || question.type} · ${question.image_count} 图`,
      state: imageQuestionState(question), title: question.title,
      course: question.course_title || `课程 ${question.course_id}`,
      answer: question.answer_preview ? `已存：${question.answer_preview}`
        : imageFailures[question.error] || question.error || "尚无已存答案",
    };
    for (const [key, value] of Object.entries(values)) {
      if (row[key].textContent !== String(value)) row[key].textContent = String(value);
    }
    row.button.setAttribute("data-review", String(question.needs_review));
    return row.item;
  });
  syncChildren(insightElement("images-list"), children);
  for (const id of imageReview.rows.keys()) if (!keep.has(id)) imageReview.rows.delete(id);
  insightElement("images-empty").hidden = children.length > 0;
  insightText("images-results", `共 ${insightCount(result.total)} 题`);
  imageReview.pages = result.pages;
  for (const field of ["total", "review", "active", "done"]) {
    insightText(`images-${field}`, insightCount(result.counts?.[field]));
  }
  const signature = JSON.stringify(result.courses || []);
  if (signature !== imageReview.courseSignature) {
    imageReview.courseSignature = signature;
    const select = insightElement("images-course");
    const selected = select.value;
    const courses = [{ id: "", title: "全部课程" }, ...(result.courses || [])];
    if (selected && !courses.some((course) => course.id === selected)) {
      courses.push({ id: selected, title: `课程 ${selected}` });
    }
    select.replaceChildren(...courses.map((course) => {
      const option = document.createElement("option");
      option.value = course.id;
      option.textContent = course.id ? `${course.title || `课程 ${course.id}`} (${course.entries ?? "--"})` : course.title;
      return option;
    }));
    select.value = selected;
  }
  const selected = keep.has(imageReview.selected) ? imageReview.selected : result.items[0]?.id || "";
  selectImageQuestion(selected);
}

function imageReviewQuery() {
  return new URLSearchParams({
    search: insightElement("images-search").value.trim(),
    course_id: insightElement("images-course").value,
    status: insightElement("images-filter-status").value,
    page: String(imageReview.page), page_size: "20",
  });
}

function scheduleImageReview() {
  clearTimeout(imageReview.timer);
  if (imageReview.connected && imageReview.view === "images") {
    imageReview.timer = setTimeout(() => void loadImageReview(), document.hidden ? 20000 : 5000);
  }
}

async function loadImageReview() {
  if (!imageReview.connected || imageReview.view !== "images" || imageReview.loading) return;
  clearTimeout(imageReview.timer);
  const generation = imageReview.generation;
  const request = ++imageReview.request;
  const current = () => generation === imageReview.generation && request === imageReview.request;
  imageReview.loading = true;
  imageReviewControls();
  try {
    const result = await backendFetch(`/image-questions?${imageReviewQuery()}`);
    if (!current()) return;
    if (imageReview.page > Math.max(1, result.pages)) {
      imageReview.page = Math.max(1, result.pages);
      imageReview.loading = false;
      return loadImageReview();
    }
    renderImageReviewList(result);
    insightError("images-error", "");
    insightText("images-status", `最近同步 ${insightDate(result.sampled_at)}`);
  } catch (error) {
    if (!current()) return;
    insightError("images-error", error.message || "图片题列表读取失败");
    insightText("images-status", "同步失败 · 保留上次数据");
  } finally {
    if (current()) {
      imageReview.loading = false;
      imageReviewControls();
      scheduleImageReview();
    }
  }
}

function imageReviewProperty(label, value) {
  const row = document.createElement("div");
  const term = document.createElement("dt");
  const definition = document.createElement("dd");
  term.textContent = label;
  definition.textContent = value;
  row.append(term, definition);
  return row;
}

function renderImageReviewDetail(question, current) {
  const diagnostics = question.diagnostics || {};
  const slots = new Map();
  insightText("images-detail-course", question.course_title || `课程 ${question.course_id}`);
  insightText("images-detail-type", `${insightTypes[question.type] || question.type} · ${question.image_count} 图`);
  insightText("images-detail-state", imageQuestionState(question));
  insightText("images-detail-quality", imageQuality(question));
  renderBankQuestionText(insightElement("images-detail-title"), question.title || "", slots);
  const options = Array.isArray(question.options) ? question.options.join("\n") : question.options || "";
  insightElement("images-options-section").hidden = !options;
  renderBankQuestionText(insightElement("images-detail-options"), options, slots);
  const attachments = (question.images || []).filter((image) => !slots.has(image.id));
  insightElement("images-attachments-section").hidden = !attachments.length;
  insightElement("images-detail-attachments").replaceChildren(...attachments.map((image) => {
    const span = document.createElement("span");
    renderBankQuestionText(span, `[${image.id}]`, slots);
    return span;
  }));
  insightElement("images-detail-answers").replaceChildren(...[
    ["已存答案", question.answers], ["DeepSeek 求解", diagnostics.solver_answers],
    ["豆包独立复核", diagnostics.verifier_answers],
  ].map(([label, values]) => {
    const row = imageReviewProperty(label, "");
    const answers = Array.isArray(values) ? values.map((answer) => question.type === "judgement"
      ? answer === "true" ? "正确" : answer === "false" ? "错误" : answer : answer) : [];
    renderBankQuestionText(row.lastElementChild, answers.length ? answers.join("\n") : "暂无结果", slots);
    return row;
  }));
  const code = question.error || diagnostics.error || "";
  insightElement("images-failure-section").hidden = !code;
  insightText("images-detail-failure", imageFailures[code] || "此次处理未通过，保留原图和可用记录供核对。");
  insightText("images-detail-failure-code", code);
  const readings = Array.isArray(diagnostics.readings) ? diagnostics.readings : [];
  insightElement("images-detail-readings").replaceChildren(...(readings.length ? readings.map((reading) => {
    const section = document.createElement("div");
    const title = document.createElement("h4");
    title.textContent = reading.id;
    const text = document.createElement("pre");
    text.textContent = reading.text;
    section.append(title, text);
    return section;
  }) : [Object.assign(document.createElement("p"), { className: "muted", textContent: "尚无识别记录" })]));
  const stages = Array.isArray(diagnostics.stages) ? diagnostics.stages : [];
  const statuses = { ok: "完成", generation_failed: "生成失败", invalid_format: "格式不合格", needs_review: "需核对" };
  insightElement("images-detail-stages").replaceChildren(...(stages.length ? stages.map((stage) => {
    const row = document.createElement("li");
    const label = document.createElement("strong");
    label.textContent = `${imageStages[stage.stage] || stage.stage} · ${statuses[stage.status] || stage.status}`;
    const meta = document.createElement("span");
    meta.textContent = `${stage.model || "--"} · 第 ${stage.attempt || 1} 次 · ${stage.seconds ?? "--"} 秒`;
    row.append(label, meta);
    return row;
  }) : [Object.assign(document.createElement("li"), { className: "muted", textContent: "尚无处理记录" })]));
  const checks = diagnostics.verification;
  const flag = (value) => value === true ? "通过" : value === false ? "未通过" : "未检查";
  insightText("images-detail-checks", checks
    ? `原图可读：${flag(checks.readable)} · 识别匹配：${flag(checks.reading_matches)} · 答案一致：${flag(diagnostics.agreement)}`
    : "");
  insightElement("images-detail-meta").replaceChildren(...[
    ["记录 ID", question.id], ["课程 ID", question.course_id],
    ["创建时间", insightDate(question.created_at)], ["更新时间", insightDate(question.updated_at)],
    ["处理耗时", diagnostics.seconds == null ? "--" : `${diagnostics.seconds} 秒`],
    ["答案状态", imageQuality(question)],
  ].map(([label, value]) => imageReviewProperty(label, value)));
  insightElement("images-detail-body").hidden = false;
  void loadBankQuestionImages(question, slots, current, "image-questions", openImageOriginal);
}

function selectImageQuestion(id) {
  const changed = id !== imageReview.selected;
  imageReview.selected = id;
  for (const [rowID, row] of imageReview.rows) row.button.setAttribute("aria-pressed", String(rowID === id));
  if (changed || !id) {
    imageReview.detailRequest += 1;
    imageReview.detailLoading = false;
    imageReview.detailVersion = "";
    insightElement("images-detail-body").hidden = true;
    insightElement("images-detail").setAttribute("aria-busy", "false");
    insightError("images-detail-error", "");
    insightText("images-detail-status", id ? "正在读取详情" : "尚未选择题目");
  }
  const question = imageReview.rows.get(id)?.question;
  if (!id || !question || imageReview.detailLoading || !imageReview.connected
    || imageQuestionVersion(question) === imageReview.detailVersion) return;
  void loadImageReviewDetail(id);
}

async function loadImageReviewDetail(id) {
  const generation = imageReview.generation;
  const request = ++imageReview.detailRequest;
  const current = () => generation === imageReview.generation && request === imageReview.detailRequest
    && imageReview.selected === id;
  imageReview.detailLoading = true;
  insightElement("images-detail").setAttribute("aria-busy", "true");
  try {
    const question = await backendFetch(`/image-questions/${encodeURIComponent(id)}`);
    if (!current()) return;
    renderImageReviewDetail(question, current);
    imageReview.detailVersion = imageQuestionVersion(question);
    insightText("images-detail-status", "");
    insightError("images-detail-error", "");
  } catch (error) {
    if (!current()) return;
    insightText("images-detail-status", "详情未更新");
    insightError("images-detail-error", error.message || "图片题详情读取失败");
  } finally {
    if (current()) {
      imageReview.detailLoading = false;
      insightElement("images-detail").setAttribute("aria-busy", "false");
    }
  }
}

function changeImageReviewFilters(debounce = false) {
  imageReview.page = 1;
  imageReview.request += 1;
  imageReview.loading = false;
  selectImageQuestion("");
  clearTimeout(imageReview.timer);
  clearTimeout(imageReview.searchTimer);
  imageReviewControls();
  if (debounce) imageReview.searchTimer = setTimeout(() => void loadImageReview(), 300);
  else void loadImageReview();
}

function imageReviewViewChanged(view) {
  imageReview.view = view;
  clearTimeout(imageReview.timer);
  clearTimeout(imageReview.searchTimer);
  if (view === "images") void loadImageReview();
}

function imageReviewConnectionChanged(connected, retain = false) {
  if (connected === imageReview.connected && (connected || retain)) return;
  imageReview.connected = connected;
  imageReview.generation += 1;
  imageReview.request += 1;
  imageReview.detailRequest += 1;
  imageReview.loading = imageReview.detailLoading = false;
  clearTimeout(imageReview.timer);
  clearTimeout(imageReview.searchTimer);
  insightElement("images-detail").setAttribute("aria-busy", "false");
  if (connected) {
    imageReview.detailVersion = "";
    void loadImageReview();
  } else if (retain) {
    insightText("images-status", "连接中断 · 保留上次数据");
  } else {
    imageReview.page = imageReview.pages = 1;
    imageReview.rows.clear();
    imageReview.courseSignature = "";
    insightElement("images-list").replaceChildren();
    insightElement("images-empty").hidden = true;
    insightElement("images-course").replaceChildren(Object.assign(document.createElement("option"), {
      value: "", textContent: "全部课程",
    }));
    selectImageQuestion("");
    for (const field of ["total", "review", "active", "done"]) insightText(`images-${field}`, "--");
    insightText("images-status", "等待连接");
    insightText("images-results", "尚未读取");
    insightError("images-error", "");
    insightElement("image-original-dialog").close();
  }
  imageReviewControls();
}

function setImageZoom(value) {
  imageReview.zoom = Math.min(400, Math.max(50, value));
  insightText("image-zoom-value", `${imageReview.zoom}%`);
  insightElement("image-original").style.width = `${imageReview.zoom}%`;
  insightElement("image-zoom-out").disabled = imageReview.zoom <= 50;
  insightElement("image-zoom-in").disabled = imageReview.zoom >= 400;
}

function openImageOriginal(image) {
  insightText("image-original-title", `原始图片 · ${image.id}`);
  const original = insightElement("image-original");
  original.src = image.data_url;
  original.alt = `题目原始图片 ${image.id}`;
  setImageZoom(100);
  const dialog = insightElement("image-original-dialog");
  if (!dialog.open) dialog.showModal();
}

insightElement("images-refresh").addEventListener("click", () => {
  imageReview.detailVersion = "";
  void loadImageReview();
});
insightElement("images-filters").addEventListener("submit", (event) => {
  event.preventDefault();
  changeImageReviewFilters();
});
insightElement("images-search").addEventListener("input", () => changeImageReviewFilters(true));
for (const id of ["images-course", "images-filter-status"]) {
  insightElement(id).addEventListener("change", () => changeImageReviewFilters());
}
insightElement("images-clear").addEventListener("click", () => {
  for (const id of ["images-search", "images-course", "images-filter-status"]) insightElement(id).value = "";
  changeImageReviewFilters();
});
for (const [id, delta] of [["images-prev", -1], ["images-next", 1]]) {
  insightElement(id).addEventListener("click", () => {
    if (imageReview.loading || imageReview.page + delta < 1 || imageReview.page + delta > imageReview.pages) return;
    imageReview.page += delta;
    void loadImageReview();
  });
}
insightElement("image-original-close").addEventListener("click", () => insightElement("image-original-dialog").close());
insightElement("image-original-dialog").addEventListener("close", () => insightElement("image-original").removeAttribute("src"));
insightElement("image-zoom-out").addEventListener("click", () => setImageZoom(imageReview.zoom - 25));
insightElement("image-zoom-in").addEventListener("click", () => setImageZoom(imageReview.zoom + 25));
document.addEventListener("visibilitychange", () => {
  if (document.hidden) scheduleImageReview();
  else if (imageReview.view === "images") void loadImageReview();
});
