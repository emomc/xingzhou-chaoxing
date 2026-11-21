const backendForm = document.querySelector("#connection-form");
const backendURL = document.querySelector("#backend-url");
const backendToken = document.querySelector("#backend-token");
const backendError = document.querySelector("#backend-error");
const backendStatus = document.querySelector("#backend-status");
const backendControls = document.querySelector("#backend-controls");
const accountForm = document.querySelector("#account-form");
const accountTarget = document.querySelector("#account-target");
const accountDialog = document.querySelector("#account-dialog");
const accountDeleteDialog = document.querySelector("#account-delete-dialog");
const accountsList = document.querySelector("#accounts-list");
const accountSearch = document.querySelector("#account-search");
const accountFilter = document.querySelector("#account-filter");
const accountsRefresh = document.querySelector("#accounts-refresh");
const addAccountButton = document.querySelector("#add-account");
const jobAccount = document.querySelector("#job-account");
const jobAccountInput = document.querySelector("#job-account-input");
const jobAccountOptions = document.querySelector("#job-account-options");
const jobAccountCombobox = document.querySelector("#job-account-combobox");
const jobForm = document.querySelector("#job-form");
const jobDialog = document.querySelector("#job-dialog");
const addJobButton = document.querySelector("#add-job");
const jobFormError = document.querySelector("#job-form-error");
const fetchCoursesButton = document.querySelector("#fetch-courses");
const courseList = document.querySelector("#course-list");
const courseStatus = document.querySelector("#course-status");
const courseError = document.querySelector("#course-error");
const courseDetailPanel = document.querySelector("#course-detail-panel");
const courseDetailTitle = document.querySelector("#course-detail-title");
const courseDetailStatus = document.querySelector("#course-detail-status");
const courseDetailSummary = document.querySelector("#course-detail-summary");
const courseDetailError = document.querySelector("#course-detail-error");
const courseDetailList = document.querySelector("#course-detail-list");
const refreshCourseDetailButton = document.querySelector("#refresh-course-detail");
const courseDetailSelection = document.querySelector("#course-detail-selection");
const selectDetailPointsButton = document.querySelector("#select-detail-points");
const clearDetailPointsButton = document.querySelector("#clear-detail-points");
const selectIncompleteCourses = document.querySelector("#select-incomplete-courses");
const clearCourseSelection = document.querySelector("#clear-course-selection");
const onlyIncompleteCourses = document.querySelector("#only-incomplete-courses");
const createJobButton = document.querySelector("#create-job");
const jobsList = document.querySelector("#jobs");
const archivedJobsList = document.querySelector("#archived-jobs");
const archivedSearch = document.querySelector("#archived-search");
const archivedRefresh = document.querySelector("#archived-refresh");
const overviewJobsList = document.querySelector("#overview-jobs");
const overviewRefresh = document.querySelector("#overview-refresh");
const tasksTab = document.querySelector("#tasks-tab");
const archivedTab = document.querySelector("#archived-tab");
const overviewTab = document.querySelector("#overview-tab");
const notesTab = document.querySelector("#notes-tab");
const jobSearch = document.querySelector("#job-search");
const jobCourseFilter = document.querySelector("#job-course-filter");
const aiChannelWeb = document.querySelector("#ai-channel-web");
const aiChannelAPI = document.querySelector("#ai-channel-api");
const aiVisionAuto = document.querySelector("#ai-vision-auto");
const aiVisionWeb = document.querySelector("#ai-vision-web");
const aiVisionAPI = document.querySelector("#ai-vision-api");
const aiVisionEnabled = document.querySelector("#ai-vision-enabled");
const aiVisionFallback = document.querySelector("#ai-vision-fallback");
const aiVisionDirect = document.querySelector("#ai-vision-direct");
const aiChannelStatus = document.querySelector("#ai-channel-status");
const aiChannelNote = document.querySelector("#ai-channel-note");
const aiChannelError = document.querySelector("#ai-channel-error");
const jobBatchButton = document.querySelector("#open-job-batch");
const jobBatchDialog = document.querySelector("#job-batch-dialog");
const jobBatchForm = document.querySelector("#job-batch-form");
const jobBatchAction = document.querySelector("#job-batch-action");
const jobBatchSearch = document.querySelector("#job-batch-search");
const jobBatchList = document.querySelector("#job-batch-list");
const jobBatchConfirm = document.querySelector("#job-batch-confirm");
const selectVisibleJobs = document.querySelector("#select-visible-jobs");
const jobBatchSelectAll = document.querySelector("#job-batch-select-all");
const jobBatchActions = {
  resume: { label: "开启", icon: "play", statuses: ["paused"], note: "保留原课程和进度，按服务器并发容量排队。" },
  pause: { label: "暂停", icon: "pause", statuses: ["queued", "starting", "running", "retry_wait"], note: "运行中的任务将先进入停止中，退出后转为暂停。" },
  retry: { label: "重试", icon: "rotate-cw", statuses: ["succeeded", "failed", "cancelled", "needs_attention"], note: "沿用原任务配置重新排队；同一账号已有活动任务时会保留失败结果。" },
  cancel: { label: "取消", icon: "square", statuses: ["queued", "starting", "running", "retry_wait", "paused", "needs_attention"], destructive: true, note: "取消后停止执行，任务记录保留。" },
  archive: { label: "归档", icon: "archive", statuses: ["succeeded", "failed", "cancelled"], note: "从课程任务列表收起，完整保留记录；可在任务归档页恢复。" },
  unarchive: { label: "恢复归档", icon: "archive-restore", statuses: ["succeeded", "failed", "cancelled"], note: "把任务重新放回课程任务列表，不会重新执行。" },
  delete: { label: "删除", icon: "trash-2", statuses: ["succeeded", "failed", "cancelled", "needs_attention"], destructive: true, note: "永久删除任务记录，不删除账号或题库。" },
};
const activeStatuses = ["queued", "starting", "running", "retry_wait", "stopping"];
const attentionStatuses = ["paused", "failed", "needs_attention"];
const finishedStatuses = ["succeeded", "failed", "cancelled", "needs_attention"];

// 拟人节奏里的任务仍然是"运行中"，但人不在电脑前，单独成一类，方便一眼看出它们在等。
function pacingWaitJob(job) {
  return job?.status === "running" && job.progress?.phase === "pacing";
}

const stateLabels = {
  queued: "排队中", starting: "启动中", running: "运行中", retry_wait: "等待重试",
  stopping: "停止中", paused: "已暂停", succeeded: "处理结束",
  failed: "失败", cancelled: "已取消", needs_attention: "需人工检查",
};
const phaseLabels = {
  login: "登录", loading_courses: "读取课程", loading_chapters: "读取章节",
  chapter: "章节任务", video: "视频任务", workid: "测验", document: "文档", read: "阅读",
  empty_chapter: "空章节已确认",
  pacing: "拟人节奏等待中",
  course_confirmed: "平台确认完成", course_unconfirmed: "平台仍有未完成任务",
};
const aiStateLabels = {
  checking_cache: "检查题库", cached: "命中题库", queued: "等待 AI 回复",
  shared_wait: "等待同题共享结果", shared: "已复用同题结果",
  running: "AI 正在处理", answered: "AI 已返回答案", error: "AI 查询失败",
  retrying: "AI 答案格式重试中",
  downloading_images: "正在读取题目图片", vision_queued: "图片题等待识图 AI",
  vision_recognizing: "豆包正在识别原图", vision_solving: "DeepSeek 正在解题",
  vision_verifying: "豆包正在独立复核原图",
  vision_review: "图片答案需核对",
  vision_running: "AI 正在识图答题", vision_unavailable: "识图 AI 未就绪",
  vision_disabled: "实验识图未开启，图片题保持未完成",
};
const videoStateLabels = {
  playing: "播放中", confirming: "已到片尾，等待平台确认",
  passed: "平台已确认通过", retrying: "媒体会话恢复中",
  replaying: "原断点未通过确认，从头补看",
  unconfirmed: "平台尚未确认通过", error: "进度上报失败",
  wait: "夜间静默，等待开窗", waiting: "夜间静默，等待开窗",
  rolled_back: "平台回退了进度，正在对齐重看",
};
const quizStateLabels = {
  querying: "查询答案中", answered: "答案已校验", submitting: "正在提交",
  submitted: "平台已接受提交", saving: "正在保存", saved: "已保存，未提交",
  preview: "仅预览，未提交", error: "未提交成功",
};
const questionTypeLabels = {
  single: "单选题", multiple: "多选题", judgement: "判断题",
  completion: "填空题", shortanswer: "简答题",
};
const errorLabels = {
  LOGIN_REQUIRED: "登录失败或需要人工验证",
  COURSE_NOT_FOUND: "未找到指定课程",
  PREVIEW_ONLY: "答案处于预览模式，测验未提交",
  INCOMPLETE_TASKS: "仍有未完成任务",
  PREVIOUS_WORKER: "需检查上次运行的进程",
  HEARTBEAT_TIMEOUT: "进程心跳超时",
  RUN_TIMEOUT: "超过任务时限",
  AI_UNAVAILABLE: "AI 暂时不可用",
  AI_RATE_LIMITED: "AI 上游限流，请等待恢复后重试",
  AI_VISION_UNAVAILABLE: "识图 AI 未就绪，请登录视觉模型或配置视觉 API",
  WORKER_EXIT: "运行进程异常退出",
  START_FAILED: "进程启动失败",
  COURSE_FETCH_FAILED: "课程拉取失败，请检查网络或账号后重试",
  LOOKUP_START_FAILED: "课程查询进程启动失败",
  LOOKUP_TIMEOUT: "课程拉取超时，请重试",
  LOOKUP_INTERRUPTED: "上次课程拉取已中断，请重新拉取",
  PREVIOUS_LOOKUP: "上次课程查询进程尚未退出，请稍后重试",
  COURSE_DETAIL_FETCH_FAILED: "任务点读取失败，请重试",
  DETAIL_LOOKUP_START_FAILED: "任务点查询进程启动失败",
  DETAIL_LOOKUP_TIMEOUT: "任务点读取超时，请重试",
  DETAIL_LOOKUP_INTERRUPTED: "上次任务点读取已中断，请重试",
  PREVIOUS_DETAIL: "上次任务点查询进程尚未退出，请稍后重试",
  DETAIL_INTERRUPTED: "上次任务点读取已中断，请重试",
  "Course lookup is in progress for this account": "该账号正在拉取课程，请稍后操作",
  "Course detail lookup is in progress for this course": "该课程任务点正在读取，请稍后",
  "Course detail lookup is in progress for this account": "该账号正在读取任务点，请稍后操作",
  "Pause or finish this account's job before fetching courses": "请先暂停或结束该账号的任务，再重新拉取课程",
  "The course lookup queue is full": "课程查询队列已满，请稍后重试",
  "The course detail lookup queue is full": "正在读取另一门课程的任务点，请稍后重试",
  "Only finished or cancelled jobs can be deleted": "只能删除已结束或已取消的任务",
  "Only finished or cancelled jobs can be archived": "只能归档已结束或已取消的任务",
  "Only finished or cancelled jobs can be restored": "只能恢复已结束或已取消的任务",
  "Restore an archived job before changing its state": "请先恢复归档任务，再更改任务状态",
  "Job is already archived": "任务已经归档",
  "Job is not archived": "任务未归档",
  "Job not found": "任务已不存在",
  "This action is not valid for the current job state": "任务状态已变化，当前操作不适用",
  "This job is not active": "任务已不在活动状态",
  "Another job is already active for this account": "该账号已有活动或暂停任务，请先处理该任务",
  "API AI channel is not configured": "API AI 通道尚未配置",
  "Invalid AI channel": "答题渠道不正确",
  "The previous worker may still be alive": "上次运行进程尚未退出，请稍后重试",
  "Check the previous worker before changing this job": "需先检查上次运行进程",
  "This account already exists": "该登录账号已存在",
  "Account not found": "账号已不存在，请刷新账号列表",
  "Cancel the account's job before changing its login identity": "请先取消该账号的任务，再更换登录账号",
  "Cancel or delete this account's jobs before deleting the account": "请先取消或删除该账号的任务，再删除账号",
  ACCOUNT_HAS_ACTIVE_JOB: "请先取消或删除该账号的任务",
  ACCOUNT_LOOKUP_BUSY: "该账号正在查询课程或任务点，请稍后删除",
  ACCOUNT_WORKER_ACTIVE: "该账号的进程尚未退出，请稍后删除",
};
let backendConnected = false;
let refreshRunning = false;
let refreshTimer;
let connectionGeneration = 0;
let closeConnectionOnSuccess = true;
let remoteAccounts = [];
let accountsFresh = false;
let accountSaving = false;
let accountDeleting = false;
let pendingDeleteAccountID = "";
let accountMutationVersion = 0;
let remoteJobCount = 0;
let archivedJobs = [];
let archivedLoading = false;
let archivedRequestGeneration = 0;
let archivedLastRefreshAt = 0;
let courseGeneration = 0;
let courseTimer;
let courseState = "idle";
let courseRequestID = null;
let courseItems = [];
let selectedCourseIDs = new Set();
let courseLoading = false;
let courseDetailTimer;
let courseDetailGeneration = 0;
let courseDetailAccountID = null;
let courseDetailCourseID = null;
let courseDetailState = "idle";
let courseDetailLoading = false;
let courseDetailData = null;
let selectedPointIDs = new Map();
let jobSubmitting = false;
let showIncompleteOnly = true;
let selectionNotice = "";
let selectedJobAccountID = "";
let jobAccountOpen = false;
let jobAccountActiveIndex = -1;
let jobAccountSearching = false;
let remoteJobs = [];
let jobFilter = "all";
const jobFilterKeys = ["all", "active", "pacing", "attention", "finished"];
let jobCourseFilterSignature = "";
let selectedJobIDs = new Set();
let jobBatchSubmitting = false;
let jobBatchConfirmationKey = "";
let jobBatchResults = new Map();
let jobMutationVersion = 0;
let aiChannelSaving = false;
let aiChannelHealth = {};
const singleJobActions = new Set();
const jobBatchViews = new Map();
let liveCourseGeneration = 0;
let liveCourseRequestCount = 0;
const liveCourseCatalogs = new Map();
const liveCourseDetails = new Map();
const liveCourseRequests = [];
const jobViews = new Map();
const overviewJobViews = new Map();
const archivedJobViews = new Map();
const archivedAccountViews = new Map();
const accountViews = new Map();
const manualTaskHistories = new Map();

function courseHasRemaining(course) {
  const summary = course && course.summary;
  return !!summary && summary.status === "ready" && Number.isInteger(summary.remaining_count)
    && summary.remaining_count > 0;
}

function pointSelection(courseID) {
  return selectedPointIDs.get(String(courseID)) || null;
}

function coursePointCount(courseID) {
  return pointSelection(courseID)?.size || 0;
}

function courseSelectable(course) {
  // 勾了具体章节就按章节学：哪怕整门课已显示完成，也能单独复查一节
  return courseHasRemaining(course) || coursePointCount(course && course.id) > 0;
}

function visibleCourses() {
  return courseItems.filter((course) => !showIncompleteOnly || courseSelectable(course));
}

function courseSummaryText(course) {
  const summary = course.summary;
  if (!summary) return "\u9700\u91cd\u65b0\u62c9\u53d6";
  if (summary.status !== "ready") return "\u4efb\u52a1\u70b9\u8bfb\u53d6\u5931\u8d25";
  if (summary.task_count === 0) return "\u65e0\u4efb\u52a1\u70b9";
  if (!(summary.remaining_count > 0)) return "\u5df2\u5b8c\u6210";
  return `\u5269\u4f59 ${summary.remaining_count}${summary.locked_count ? ` \u00b7 \u9501\u5b9a ${summary.locked_count}` : ""}`;
}

function updateCourseControls() {
  if (!selectedJobAccountID && jobAccount.value) {
    selectedJobAccountID = jobAccount.value;
    const selected = remoteAccounts.find((account) => account.id === jobAccount.value);
    if (selected) jobAccountInput.value = jobAccountDisplay(selected);
  }
  const ready = backendConnected && accountsFresh;
  const locked = !ready || jobSubmitting || jobBatchSubmitting || singleJobActions.size > 0;
  const pending = courseLoading || ["queued", "running"].includes(courseState);
  const learnable = courseItems.filter(courseSelectable);
  const finished = courseItems.filter((course) => course.summary
    && course.summary.status === "ready" && course.summary.task_count > 0 && course.summary.remaining_count === 0);
  const noTasks = courseItems.filter((course) => course.summary
    && course.summary.status === "ready" && course.summary.task_count === 0);
  const byPoint = courseItems.filter((course) => coursePointCount(course.id) > 0).length;
  addJobButton.disabled = locked;
  jobAccount.disabled = locked;
  jobAccountInput.disabled = locked;
  jobForm.setAttribute("aria-busy", String(jobSubmitting));
  document.querySelector("#job-connection-error").hidden = ready;
  document.querySelector("#manage-accounts").disabled = jobSubmitting;
  document.querySelector("#videos-only").disabled = locked;
  document.querySelector("#auto-submit").disabled = locked || document.querySelector("#videos-only").checked;
  document.querySelector("#experimental-vision").disabled = locked;
  document.querySelector("#human-pacing").disabled = locked;
  for (const id of ["job-editor-close", "job-editor-cancel"]) document.querySelector(`#${id}`).disabled = jobSubmitting;
  setText(document.querySelector("#create-job-label"), jobSubmitting ? "创建中…" : "创建任务");
  fetchCoursesButton.disabled = locked || !selectedJobAccountID || pending;
  fetchCoursesButton.textContent = pending ? "\u62c9\u53d6\u4e2d\u2026" : courseState === "idle" ? "\u62c9\u53d6\u8bfe\u7a0b" : "\u91cd\u65b0\u62c9\u53d6";
  selectIncompleteCourses.disabled = locked || pending || !learnable.length;
  clearCourseSelection.disabled = locked || pending || !selectedCourseIDs.size;
  onlyIncompleteCourses.disabled = locked || pending || !courseItems.length;
  onlyIncompleteCourses.checked = showIncompleteOnly;
  document.querySelector("#course-selection-count").textContent =
    `\u5df2\u9009 ${selectedCourseIDs.size} / \u53ef\u5b66\u4e60 ${learnable.length} \u00b7 \u5df2\u5b8c\u6210 ${finished.length}${noTasks.length ? ` \u00b7 \u65e0\u4efb\u52a1\u70b9 ${noTasks.length}` : ""}${byPoint ? ` \u00b7 \u6309\u7ae0\u8282 ${byPoint} \u95e8` : ""}`;
  courseList.setAttribute("aria-busy", String(pending));
  for (const checkbox of courseList.querySelectorAll("input")) {
    const course = courseItems.find((item) => item.id === checkbox.value);
    checkbox.disabled = locked || pending || !courseSelectable(course);
  }
  for (const button of courseList.querySelectorAll("button")) button.disabled = locked || pending;
  createJobButton.disabled = locked || pending || courseDetailLoading || !selectedJobAccountID
    || courseState !== "done" || !selectedCourseIDs.size || selectedCourseIDs.size > 50;
  refreshCourseDetailButton.disabled = locked || !courseDetailCourseID || courseDetailLoading;
  selectDetailPointsButton.disabled = locked || courseDetailLoading || !courseDetailData;
  clearDetailPointsButton.disabled = locked || !courseDetailCourseID || !coursePointCount(courseDetailCourseID);
  if (courseState === "done") {
    const limit = selectedCourseIDs.size > 50 ? "\u5355\u4e2a\u4efb\u52a1\u6700\u591a\u9009\u62e9 50 \u95e8\u8bfe\u7a0b" : "";
    courseError.textContent = selectionNotice || limit;
    courseError.hidden = !courseError.textContent;
  }
  renderJobSelection();
}

function resetCourseDetail() {
  clearTimeout(courseDetailTimer);
  courseDetailGeneration += 1;
  courseDetailAccountID = null;
  courseDetailCourseID = null;
  courseDetailState = "idle";
  courseDetailLoading = false;
  courseDetailData = null;
  courseDetailPanel.hidden = true;
  courseDetailTitle.textContent = "任务点详情";
  courseDetailStatus.textContent = "请选择课程查看任务点";
  courseDetailSummary.textContent = "";
  courseDetailSelection.textContent = "未勾选章节：整门课都学";
  courseDetailError.hidden = true;
  courseDetailError.textContent = "";
  courseDetailList.replaceChildren();
  refreshCourseDetailButton.disabled = true;
}

function resetCourses(clearList = true) {
  clearTimeout(courseTimer);
  courseGeneration += 1;
  resetCourseDetail();
  courseState = "idle";
  courseRequestID = null;
  courseLoading = false;
  if (clearList) {
    courseItems = [];
    selectedCourseIDs = new Set();
    selectedPointIDs = new Map();
    courseList.replaceChildren();
  }
  courseError.hidden = true;
  jobFormError.hidden = true;
  selectionNotice = "";
  courseStatus.textContent = selectedJobAccountID ? "尚未拉取课程" : "尚未选择账号";
  updateCourseControls();
}

function renderCourses(courses) {
  courseItems = courses;
  selectedCourseIDs = new Set([...selectedCourseIDs].filter((id) => (
    courseSelectable(courses.find((course) => course.id === id))
  )));
  const visible = visibleCourses();
  courseList.replaceChildren(...visible.map((course) => {
    const selectable = courseSelectable(course);
    const row = document.createElement("div");
    row.className = `course-option${selectable ? "" : " is-disabled"}`;
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.value = course.id;
    checkbox.checked = selectedCourseIDs.has(course.id);
    checkbox.disabled = !selectable;
    checkbox.addEventListener("change", () => {
      if (!courseSelectable(course)) {
        checkbox.checked = false;
        selectedCourseIDs.delete(course.id);
      } else if (checkbox.checked) selectedCourseIDs.add(course.id);
      else selectedCourseIDs.delete(course.id);
      selectionNotice = "";
      updateCourseControls();
    });
    const info = document.createElement("span");
    info.className = "course-info";
    const title = document.createElement("span");
    title.className = "course-title";
    title.textContent = course.title;
    const meta = document.createElement("span");
    meta.className = "course-meta";
    const picked = coursePointCount(course.id);
    meta.textContent = [course.teacher, courseSummaryText(course),
      picked ? `\u53ea\u5b66 ${picked} \u7ae0` : "", `ID ${course.id}`].filter(Boolean).join(" \u00b7 ");
    info.append(title, meta);
    const detailButton = document.createElement("button");
    detailButton.type = "button";
    detailButton.className = "ghost course-detail-button";
    detailButton.textContent = "\u4efb\u52a1\u70b9";
    detailButton.addEventListener("click", () => loadCourseDetail(course.id));
    row.append(checkbox, info, detailButton);
    return row;
  }));
  if (!visible.length && courses.length) {
    const empty = document.createElement("p");
    empty.className = "course-detail-empty";
    empty.textContent = showIncompleteOnly ? "\u6ca1\u6709\u672a\u5b8c\u6210\u8bfe\u7a0b" : "\u6ca1\u6709\u8bfe\u7a0b";
    courseList.append(empty);
  }
}

function renderCourseDetail(result) {
  const detail = result.detail;
  if (!detail || !detail.course || !Array.isArray(detail.points) || !detail.totals) {
    throw new Error("任务点详情格式不正确，请重试");
  }
  const { totals, course, points } = detail;
  if (!["task_count", "completed_count", "remaining_count", "point_count"].every(
    (key) => Number.isInteger(totals[key]) && totals[key] >= 0
  ) || totals.point_count !== points.length) {
    throw new Error("任务点统计格式不正确，请重试");
  }
  const courseID = String(course.id);
  courseDetailData = detail;
  // 章节列表变了就把已经不存在的勾选去掉，避免带着过期 ID 建任务
  const available = new Set(points.map((point) => String(point.id)));
  const picked = pointSelection(courseID);
  if (picked) {
    for (const pointID of [...picked]) if (!available.has(pointID)) picked.delete(pointID);
    if (!picked.size) selectedPointIDs.delete(courseID);
  }
  courseDetailTitle.textContent = course.title || "任务点详情";
  courseDetailSummary.textContent = `共 ${totals.point_count} 节 · 任务点 ${totals.task_count} · 已完成 ${totals.completed_count} · 剩余 ${totals.remaining_count}`;
  courseDetailList.replaceChildren(...points.map((point) => {
    const pointID = String(point.id);
    const selectable = !point.locked && point.task_count > 0;
    const checked = !!pointSelection(courseID)?.has(pointID);
    const row = document.createElement("div");
    row.className = `course-detail-item is-selectable${point.completed ? " is-complete" : ""}`
      + `${point.locked ? " is-locked" : ""}${checked ? " is-selected" : ""}`;
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.value = pointID;
    checkbox.checked = checked;
    checkbox.disabled = !selectable;
    checkbox.setAttribute("aria-label", `只学这一节：${point.title}`);
    checkbox.addEventListener("change", () => {
      togglePointSelection(courseID, pointID, checkbox.checked);
    });
    const title = document.createElement("span");
    title.className = "course-detail-item-title";
    title.textContent = point.title;
    const meta = document.createElement("span");
    meta.className = "course-detail-item-meta";
    meta.textContent = point.locked
      ? "已锁定"
      : point.task_count === 0
        ? "无任务点"
        : point.completed
          ? `已完成 ${point.completed_count} / ${point.task_count}`
          : `已完成 ${point.completed_count} / ${point.task_count} · 剩余 ${point.remaining_count}`;
    row.append(checkbox, title, meta);
    return row;
  }));
  if (!points.length) {
    const empty = document.createElement("p");
    empty.className = "course-detail-empty";
    empty.textContent = "没有读取到任务点";
    courseDetailList.append(empty);
  }
  renderPointSelectionSummary(courseID);
}

function renderPointSelectionSummary(courseID) {
  const count = courseID ? coursePointCount(courseID) : 0;
  const total = courseDetailData ? courseDetailData.points.length : 0;
  courseDetailSelection.textContent = count
    ? `已勾选 ${count} / ${total} 节 · 任务只学这几节`
    : "未勾选章节：整门课都学";
}

function togglePointSelection(courseID, pointID, checked) {
  const key = String(courseID);
  const course = courseItems.find((item) => item.id === key);
  if (checked) {
    let picked = selectedPointIDs.get(key);
    if (!picked) {
      picked = new Set();
      selectedPointIDs.set(key, picked);
    }
    picked.add(pointID);
    // 勾了章节就等于勾了这门课，不用再单独点课程前面的复选框
    if (course) selectedCourseIDs.add(key);
  } else {
    const picked = selectedPointIDs.get(key);
    if (picked) picked.delete(pointID);
    if (!picked || !picked.size) {
      selectedPointIDs.delete(key);
      if (course && !courseHasRemaining(course)) selectedCourseIDs.delete(key);
    }
  }
  selectionNotice = "";
  renderCourses(courseItems);
  if (courseDetailData) renderCourseDetail({ detail: courseDetailData });
  updateCourseControls();
}

function selectAllDetailPoints() {
  const courseID = courseDetailCourseID;
  if (!courseID || !courseDetailData) return;
  const picked = new Set(pointSelection(courseID) || []);
  for (const point of courseDetailData.points) {
    if (!point.locked && point.task_count > 0 && point.remaining_count > 0) picked.add(String(point.id));
  }
  if (!picked.size) return;
  selectedPointIDs.set(String(courseID), picked);
  const course = courseItems.find((item) => item.id === String(courseID));
  if (course) selectedCourseIDs.add(String(courseID));
  selectionNotice = "";
  renderCourses(courseItems);
  renderCourseDetail({ detail: courseDetailData });
  updateCourseControls();
}

function clearDetailPoints() {
  const courseID = courseDetailCourseID;
  if (!courseID) return;
  selectedPointIDs.delete(String(courseID));
  const course = courseItems.find((item) => item.id === String(courseID));
  if (course && !courseHasRemaining(course)) selectedCourseIDs.delete(String(courseID));
  selectionNotice = "";
  renderCourses(courseItems);
  if (courseDetailData) renderCourseDetail({ detail: courseDetailData });
  updateCourseControls();
}

function currentCourseDetail(generation, accountID, courseID) {
  return generation === courseDetailGeneration && accountID === jobAccount.value
    && accountID === courseDetailAccountID && courseID === courseDetailCourseID;
}

function applyCourseDetailResult(result, generation, accountID, courseID) {
  if (!currentCourseDetail(generation, accountID, courseID)) return;
  if (result.account_id !== accountID || result.course_id !== courseID
      || !["idle", "queued", "running", "done", "error"].includes(result.state)) {
    throw new Error("任务点响应不正确，请重试");
  }
  courseDetailState = result.state;
  courseDetailPanel.hidden = false;
  if (result.detail) renderCourseDetail(result);
  if (result.state === "done") {
    courseDetailLoading = false;
    courseDetailStatus.textContent = "任务点读取完成";
    courseDetailError.hidden = true;
  } else if (result.state === "error") {
    courseDetailLoading = false;
    courseDetailStatus.textContent = result.detail ? "读取失败，显示上次结果" : "任务点读取失败";
    courseDetailError.textContent = errorLabels[result.error_code] || "任务点读取失败，请重试";
    courseDetailError.hidden = false;
  } else if (["queued", "running"].includes(result.state)) {
    courseDetailLoading = true;
    courseDetailStatus.textContent = result.state === "queued" ? "任务点查询排队中…" : "正在读取任务点…";
    courseDetailTimer = setTimeout(() => pollCourseDetail(generation, accountID, courseID),
      document.hidden ? 3000 : 1000);
  } else {
    courseDetailLoading = false;
    courseDetailStatus.textContent = "尚未读取任务点";
  }
  updateCourseControls();
}

async function pollCourseDetail(generation, accountID, courseID) {
  if (!currentCourseDetail(generation, accountID, courseID)) return;
  try {
    const result = await backendFetch(`/accounts/${accountID}/courses/${courseID}`);
    applyCourseDetailResult(result, generation, accountID, courseID);
  } catch (error) {
    if (!currentCourseDetail(generation, accountID, courseID)) return;
    courseDetailLoading = false;
    courseDetailState = "error";
    courseDetailStatus.textContent = "任务点读取失败";
    courseDetailError.textContent = error.message || "任务点读取失败，请重试";
    courseDetailError.hidden = false;
    updateCourseControls();
  }
}

async function loadCourseDetail(courseID) {
  clearTimeout(courseDetailTimer);
  courseDetailGeneration += 1;
  const generation = courseDetailGeneration;
  const accountID = jobAccount.value;
  const switched = String(courseDetailCourseID) !== String(courseID);
  courseDetailAccountID = accountID;
  courseDetailCourseID = courseID;
  courseDetailState = "queued";
  courseDetailLoading = true;
  courseDetailData = null;
  courseDetailPanel.hidden = false;
  courseDetailError.hidden = true;
  courseDetailStatus.textContent = "正在请求任务点…";
  courseDetailTitle.textContent = courseItems.find((course) => course.id === courseID)?.title || "任务点详情";
  if (switched) {
    // 换课程时先清空，别让上一门课的章节留在列表里被误勾
    courseDetailList.replaceChildren();
    courseDetailSummary.textContent = "";
    renderPointSelectionSummary(String(courseID));
  }
  updateCourseControls();
  try {
    const result = await backendFetch(`/accounts/${accountID}/courses/${courseID}`, {
      method: "POST", body: "{}",
    });
    applyCourseDetailResult(result, generation, accountID, courseID);
  } catch (error) {
    if (!currentCourseDetail(generation, accountID, courseID)) return;
    courseDetailLoading = false;
    courseDetailState = "error";
    courseDetailStatus.textContent = "任务点读取失败";
    courseDetailError.textContent = error.message || "任务点读取失败，请重试";
    courseDetailError.hidden = false;
    updateCourseControls();
  }
}

async function loadCourses(refresh = false) {
  clearTimeout(courseTimer);
  if (refresh) resetCourses(false);
  if (!backendConnected || !jobAccount.value) return;
  const accountID = jobAccount.value;
  const generation = courseGeneration;
  const connection = connectionGeneration;
  const current = () => generation === courseGeneration
    && connection === connectionGeneration && accountID === jobAccount.value;
  courseLoading = true;
  if (refresh) courseStatus.textContent = "正在拉取课程…";
  courseError.hidden = true;
  updateCourseControls();
  try {
    const result = await backendFetch(`/accounts/${accountID}/courses`, refresh
      ? { method: "POST", body: "{}" } : {});
    if (!current()) return;
    if (result.account_id !== accountID || !["idle", "queued", "running", "done", "error"].includes(result.state)) {
      throw new Error("课程响应不正确，请重新拉取");
    }
    if (courseRequestID !== result.request_id && !courseItems.length) {
      selectedCourseIDs = new Set();
      renderCourses([]);
    }
    courseRequestID = result.request_id;
    courseState = result.state;
    if (courseState === "done") {
      if (!Array.isArray(result.courses) || result.courses.some(
        (course) => !course || !/^[0-9]{1,20}$/.test(course.id) || typeof course.title !== "string"
      )) throw new Error("课程列表格式不正确，请重新拉取");
      renderCourses(result.courses);
      courseStatus.textContent = result.courses.length
        ? `已拉取 ${result.courses.length} 门课程 · ${new Date(result.updated_at * 1000).toLocaleString()}`
        : "该账号暂无可选课程";
    } else if (courseState === "error") {
      courseStatus.textContent = courseItems.length
        ? "重新拉取失败，仍显示上次课程列表"
        : "未取得课程列表";
      courseError.textContent = errorLabels[result.error_code] || "课程拉取失败，请重试";
      courseError.hidden = false;
    } else if (["queued", "running"].includes(courseState)) {
      courseStatus.textContent = courseState === "queued"
        ? (courseItems.length ? "课程查询排队中，当前列表仍可用…" : "课程查询排队中…")
        : (courseItems.length ? "正在重新拉取，当前列表仍可用…" : "正在拉取课程…");
      courseTimer = setTimeout(() => loadCourses(), document.hidden ? 3000 : 1000);
    } else {
      courseStatus.textContent = "尚未拉取课程";
    }
  } catch (error) {
    if (!current()) return;
    courseState = "error";
    courseStatus.textContent = "未取得课程列表";
    courseError.textContent = error.message || "课程拉取失败";
    courseError.hidden = false;
  } finally {
    if (current()) {
      courseLoading = false;
      updateCourseControls();
    }
  }
}

fetchCoursesButton.addEventListener("click", () => loadCourses(true));
refreshCourseDetailButton.addEventListener("click", () => {
  if (courseDetailCourseID) loadCourseDetail(courseDetailCourseID);
});
selectDetailPointsButton.addEventListener("click", () => selectAllDetailPoints());
clearDetailPointsButton.addEventListener("click", () => clearDetailPoints());
jobAccountInput.addEventListener("focus", () => {
  openJobAccountOptions();
  jobAccountInput.select();
});
jobAccountInput.addEventListener("input", () => {
  jobAccountSearching = true;
  const selected = remoteAccounts.find((account) => account.id === selectedJobAccountID);
  if (jobAccount.value && selected && jobAccountInput.value !== jobAccountDisplay(selected)) {
    selectedJobAccountID = "";
    jobAccount.value = "";
    resetCourses();
    updateCourseControls();
  }
  renderJobAccountOptions(true);
});
jobAccountInput.addEventListener("keydown", (event) => {
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    if (!jobAccountOpen) openJobAccountOptions();
    moveJobAccountActive(event.key === "ArrowDown" ? 1 : -1);
  } else if (event.key === "Enter" && jobAccountOpen) {
    const option = jobAccountOptions.querySelector('[data-active="true"]')
      || jobAccountOptions.querySelector(".account-option");
    if (option) {
      event.preventDefault();
      setJobAccountSelection(option.getAttribute("data-account-id"));
    }
  } else if (event.key === "Escape") {
    closeJobAccountOptions(true);
  }
});
jobAccountInput.addEventListener("blur", () => {
  setTimeout(() => closeJobAccountOptions(), 120);
});
jobAccountOptions.addEventListener("pointerdown", (event) => event.preventDefault());
selectIncompleteCourses.addEventListener("click", () => {
  const visible = visibleCourses().filter(courseSelectable);
  selectedCourseIDs = new Set(visible.slice(0, 50).map((course) => course.id));
  selectionNotice = visible.length > 50 ? "可学习课程超过 50 门，已选择前 50 门" : "";
  renderCourses(courseItems);
  updateCourseControls();
});
clearCourseSelection.addEventListener("click", () => {
  selectedCourseIDs = new Set();
  selectedPointIDs = new Map();
  selectionNotice = "";
  renderCourses(courseItems);
  if (courseDetailData) renderCourseDetail({ detail: courseDetailData });
  updateCourseControls();
});
onlyIncompleteCourses.addEventListener("change", () => {
  showIncompleteOnly = onlyIncompleteCourses.checked;
  selectionNotice = "";
  renderCourses(courseItems);
  updateCourseControls();
});

function selectView(view) {
  if (typeof consoleSelectView === "function") {
    consoleSelectView(view);
  } else {
    for (const name of ["overview", "tasks", "archived", "accounts", "notes"]) {
      document.querySelector(`#${name}-view`).hidden = name !== view;
      document.querySelector(`#${name}-tab`).setAttribute("aria-selected", String(name === view));
    }
  }
  const count = document.querySelector("#count");
  count.hidden = !["tasks", "archived", "notes", "accounts"].includes(view);
  if (view === "tasks") {
    count.textContent = `${remoteJobCount} 个任务`;
    renderJobs();
    refreshTaskCourses();
    void refreshManualTaskHistory();
  }
  if (view === "archived") {
    count.textContent = `${archivedJobs.length} 个已归档`;
    renderArchivedJobs();
  }
  if (view === "notes") count.textContent = `${noteCount} 条`;
  if (view === "accounts") {
    count.textContent = `${remoteAccounts.length} 个账号`;
    renderAccountCards();
  }
}
overviewTab.addEventListener("click", () => selectView("overview"));
tasksTab.addEventListener("click", () => selectView("tasks"));
archivedTab.addEventListener("click", () => selectView("archived"));
notesTab.addEventListener("click", () => selectView("notes"));
document.querySelector("#accounts-tab").addEventListener("click", () => selectView("accounts"));
document.querySelector("#manage-accounts").addEventListener("click", () => {
  if (jobSubmitting) return;
  closeJobDialog();
  selectView("accounts");
});
document.querySelector("#overview-open-tasks").addEventListener("click", () => selectView("tasks"));
overviewRefresh.addEventListener("click", () => void refreshBackend());

function openJobDialog(accountID = "") {
  if (!canMutateJobs()) return;
  if (accountID) {
    if (!remoteAccounts.some((account) => account.id === accountID)) return;
    if (selectedJobAccountID !== accountID) setJobAccountSelection(accountID, { load: false });
  }
  selectView("tasks");
  updateCourseControls();
  if (!jobDialog.open) jobDialog.showModal();
  jobAccountInput.focus();
  if (accountID) closeJobAccountOptions();
  if (selectedJobAccountID && courseState === "idle" && !courseLoading) void loadCourses();
}

function closeJobDialog(force = false) {
  if ((!jobSubmitting || force) && jobDialog.open) jobDialog.close();
}

function showJobFormError(message) {
  setText(jobFormError, message);
  jobFormError.hidden = false;
  jobFormError.focus();
}

addJobButton.addEventListener("click", () => openJobDialog());
for (const id of ["job-editor-close", "job-editor-cancel"]) {
  document.querySelector(`#${id}`).addEventListener("click", () => closeJobDialog());
}
jobDialog.addEventListener("cancel", (event) => {
  if (jobSubmitting) event.preventDefault();
});

async function backendFetch(path, options = {}) {
  const response = await fetch("/api/backend" + path, {
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
    signal: AbortSignal.timeout(18000),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(errorLabels[body.error] || body.error || `请求失败 (${response.status})`);
  return body;
}

async function runBackendAction(button, action) {
  button.disabled = true;
  backendError.hidden = true;
  try {
    await action();
  } catch (error) {
    backendError.textContent = error.message || "请求失败";
    backendError.hidden = false;
  } finally {
    if (button.isConnected) button.disabled = false;
  }
}

backendForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const token = backendToken.value;
  backendToken.value = "";
  runBackendAction(backendForm.querySelector("button[type=submit]"), async () => {
    await backendFetch("", {
      method: "PUT", body: JSON.stringify({ url: backendURL.value, token }),
    });
    connectionGeneration += 1;
    if (typeof insightsConnectionChanged === "function") insightsConnectionChanged(false);
    closeConnectionOnSuccess = true;
    backendConnected = true;
    accountsFresh = false;
    closeJobDialog(true);
    resetJobBatch();
    closeAccountDialogs();
    remoteAccounts = [];
    selectedJobAccountID = "";
    jobAccount.value = "";
    jobAccountInput.value = "";
    jobAccountSearching = false;
    closeJobAccountOptions();
    remoteJobs = [];
    jobCourseFilter.value = "";
    jobCourseFilterSignature = "";
    remoteJobCount = 0;
    resetArchivedJobs();
    manualTaskHistories.clear();
    renderJobs();
    resetLiveCourse();
    resetCourses();
    await refreshBackend();
  });
});

document.querySelector("#disconnect-backend").addEventListener("click", (event) => {
  runBackendAction(event.currentTarget, async () => {
    await backendFetch("", { method: "DELETE" });
    connectionGeneration += 1;
    backendConnected = false;
    accountsFresh = false;
    closeJobDialog(true);
    resetJobBatch();
    closeAccountDialogs();
    if (typeof insightsConnectionChanged === "function") insightsConnectionChanged(false);
    overviewRefresh.disabled = true;
    renderAccounts([]);
    resetCourses();
    clearTimeout(refreshTimer);
    backendControls.hidden = true;
    jobsList.replaceChildren();
    jobViews.clear();
    resetArchivedJobs();
    resetLiveCourse();
    remoteJobCount = 0;
    remoteJobs = [];
    jobCourseFilter.value = "";
    jobCourseFilterSignature = "";
    manualTaskHistories.clear();
    if (!document.querySelector("#tasks-view").hidden) document.querySelector("#count").textContent = "0 个任务";
    backendStatus.textContent = "未连接";
    updateDashboard({});
    setSyncState(false, "未连接");
    setText(document.querySelector("#last-sync"), "尚未同步");
    renderJobs();
    renderOverviewJobs();
  });
});

accountForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (accountSaving || !backendConnected || !accountsFresh) return;
  const password = document.querySelector("#account-password").value;
  document.querySelector("#account-password").value = "";
  const fields = {
    label: document.querySelector("#account-label").value,
    username: document.querySelector("#account-username").value,
    password,
  };
  const id = accountTarget.value;
  const generation = connectionGeneration;
  const error = document.querySelector("#account-form-error");
  accountSaving = true;
  error.hidden = true;
  updateAccountDialogControls();
  renderAccountCards();
  try {
    await backendFetch("/accounts" + (id ? "/" + id : ""), {
      method: id ? "PUT" : "POST", body: JSON.stringify(fields),
    });
    if (generation !== connectionGeneration) return;
    accountMutationVersion += 1;
    accountDialog.close();
    accountForm.reset();
    await refreshBackend();
  } catch (failure) {
    if (generation !== connectionGeneration) return;
    setText(error, failure.message || "保存账号失败");
    error.hidden = false;
  } finally {
    accountSaving = false;
    updateAccountDialogControls();
    renderAccountCards();
  }
});

document.querySelector("#videos-only").addEventListener("change", (event) => {
  const submit = document.querySelector("#auto-submit");
  submit.disabled = event.target.checked;
  if (event.target.checked) submit.checked = false;
});

jobForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (jobSubmitting || jobBatchSubmitting || singleJobActions.size) return;
  if (!backendConnected || !accountsFresh) {
    showJobFormError("服务连接不可用，请等待连接恢复后再创建任务");
    return;
  }
  const videosOnly = document.querySelector("#videos-only").checked;
  const submit = !videosOnly && document.querySelector("#auto-submit").checked;
  const experimentalVision = document.querySelector("#experimental-vision").checked;
  const humanPacing = document.querySelector("#human-pacing").checked;
  const courseIDs = courseItems.filter((course) => selectedCourseIDs.has(course.id) && courseSelectable(course))
    .map((course) => course.id);
  const pointIDs = {};
  for (const courseID of courseIDs) {
    const picked = pointSelection(courseID);
    if (picked && picked.size) pointIDs[courseID] = [...picked];
  }
  const accountID = jobAccount.value;
  if (courseState !== "done" || courseLoading || courseDetailLoading || !accountID
      || !courseIDs.length || courseIDs.length > 50) {
    showJobFormError(courseDetailLoading
      ? "请等待任务点读取完成后再创建任务"
      : "请拉取课程并选择 1 至 50 门课程");
    return;
  }
  if (typeof refreshSession === "function") await refreshSession().catch(() => {});
  const sessionUser = typeof currentUser !== "undefined" ? currentUser : null;
  const quotaRequired = sessionUser && sessionUser.role !== "owner";
  if (quotaRequired) {
    const planActive = sessionUser.plan_active
      && Number(sessionUser.plan_expires_at) > Date.now() / 1000;
    if (planActive && courseIDs.length > Number(sessionUser.plan_course_limit || 0)) {
      showJobFormError(`月度套餐单次最多 ${sessionUser.plan_course_limit} 门课程`);
      return;
    }
    if (!planActive && Number(sessionUser.credits || 0) < courseIDs.length) {
      showJobFormError(`剩余课程额度不足（剩余 ${sessionUser.credits || 0} 门）`);
      return;
    }
    const chapterNote = Object.keys(pointIDs).length
      ? `其中 ${Object.keys(pointIDs).length} 门只学你勾选的章节。\n` : "";
    const message = planActive
      ? `本次将使用月度套餐创建 ${courseIDs.length} 门课程（单次上限 ${sessionUser.plan_course_limit} 门）。\n${chapterNote}创建后即使取消或删除也不会返还次数。确认继续？`
      : `本次将消耗 ${courseIDs.length} 门课程额度，创建后剩余 ${Number(sessionUser.credits || 0) - courseIDs.length} 门。\n${chapterNote}即使取消任务也不会返还，确认继续？`;
    if (!confirm(message)) return;
  }
  const generation = connectionGeneration;
  const draftGeneration = courseGeneration;
  jobSubmitting = true;
  jobFormError.hidden = true;
  updateCourseControls();
  try {
    await backendFetch("/jobs", {
      method: "POST",
      headers: { "Idempotency-Key": crypto.randomUUID() },
      body: JSON.stringify({
        account_id: accountID, course_ids: courseIDs,
        videos_only: videosOnly, submit, preview: !submit && !videosOnly,
        experimental_vision: experimentalVision, human_pacing: humanPacing,
        ...(Object.keys(pointIDs).length ? { point_ids: pointIDs } : {}),
        ...(quotaRequired ? { quota_confirmed: true } : {}),
      }),
    });
    if (generation !== connectionGeneration) return;
    if (draftGeneration === courseGeneration && accountID === jobAccount.value) {
      selectedCourseIDs = new Set();
      selectedPointIDs = new Map();
      selectionNotice = "";
      renderCourses(courseItems);
    }
    closeJobDialog(true);
    jobSearch.value = "";
    setJobFilter("all");
    selectView("tasks");
    await refreshBackend();
    if (typeof refreshSession === "function") void refreshSession();
  } catch (error) {
    if (generation === connectionGeneration) showJobFormError(error.message || "创建任务失败，请重试");
  } finally {
    jobSubmitting = false;
    updateCourseControls();
  }
});

function jobAccountDisplay(account) {
  return `${account.label || "未命名账号"} (${account.username_masked || "账号已加密"})`;
}

function closeJobAccountOptions(restore = false) {
  jobAccountOpen = false;
  jobAccountActiveIndex = -1;
  jobAccountOptions.hidden = true;
  jobAccountCombobox.setAttribute("aria-expanded", "false");
  if (restore) {
    const selected = remoteAccounts.find((account) => account.id === selectedJobAccountID);
    jobAccountInput.value = selected ? jobAccountDisplay(selected) : "";
  }
}

function openJobAccountOptions() {
  if (!backendConnected || !accountsFresh || jobAccountInput.disabled) return;
  jobAccountOpen = true;
  jobAccountOptions.hidden = false;
  jobAccountCombobox.setAttribute("aria-expanded", "true");
  renderJobAccountOptions();
}

function renderJobAccountOptions(show = jobAccountOpen) {
  if (show) {
    jobAccountOpen = true;
    jobAccountOptions.hidden = false;
    jobAccountCombobox.setAttribute("aria-expanded", "true");
  }
  const rawQuery = jobAccountInput.value.trim();
  const selected = remoteAccounts.find((account) => account.id === selectedJobAccountID);
  const query = (selected && rawQuery === jobAccountDisplay(selected) ? "" : rawQuery)
    .toLocaleLowerCase();
  const matches = remoteAccounts.filter((account) => {
    const searchable = [account.label, account.username_masked, account.id]
      .filter(Boolean).join(" ").toLocaleLowerCase();
    return !query || searchable.includes(query);
  });
  const nodes = matches.slice(0, 100).map((account, index) => {
    const option = document.createElement("button");
    option.type = "button";
    option.className = "account-option";
    option.id = `job-account-option-${account.id}`;
    option.setAttribute("role", "option");
    option.setAttribute("data-account-id", account.id);
    option.setAttribute("aria-selected", String(account.id === selectedJobAccountID));
    if (show && index === jobAccountActiveIndex) option.setAttribute("data-active", "true");
    const label = document.createElement("strong");
    label.textContent = account.label || "未命名账号";
    const meta = document.createElement("span");
    const shortID = account.id.length > 8 ? account.id.slice(-8) : account.id;
    meta.textContent = `${account.username_masked || "账号已加密"} · ID …${shortID}`;
    option.append(label, meta);
    option.addEventListener("click", () => setJobAccountSelection(account.id));
    return option;
  });
  if (!nodes.length) {
    const empty = document.createElement("div");
    empty.className = "account-option-empty";
    empty.textContent = remoteAccounts.length ? "没有匹配的账号" : "暂无账号";
    nodes.push(empty);
  } else if (matches.length > 100) {
    const more = document.createElement("div");
    more.className = "account-option-empty";
    more.textContent = `还有 ${matches.length - 100} 个账号，请继续输入缩小范围`;
    nodes.push(more);
  }
  jobAccountOptions.replaceChildren(...nodes);
  if (jobAccountActiveIndex >= nodes.length) jobAccountActiveIndex = nodes.length - 1;
  jobAccountCombobox.setAttribute("aria-expanded", String(show));
}

function moveJobAccountActive(direction) {
  const options = jobAccountOptions.querySelectorAll("button");
  if (!options.length) return;
  jobAccountActiveIndex = (jobAccountActiveIndex + direction + options.length) % options.length;
  for (const [index, option] of options.entries()) {
    option.setAttribute("data-active", String(index === jobAccountActiveIndex));
  }
  jobAccountInput.setAttribute("aria-activedescendant", options[jobAccountActiveIndex].id);
}

function setJobAccountSelection(accountID, { load = true, focus = false } = {}) {
  const account = remoteAccounts.find((item) => item.id === accountID);
  if (!account) return;
  const previous = selectedJobAccountID;
  selectedJobAccountID = account.id;
  jobAccount.value = account.id;
  jobAccountInput.value = jobAccountDisplay(account);
  jobAccountSearching = false;
  jobAccountInput.removeAttribute("aria-activedescendant");
  closeJobAccountOptions();
  if (previous !== account.id) {
    resetCourses();
    if (load) void loadCourses();
  }
  if (focus) jobAccountInput.focus();
  updateCourseControls();
}

function renderAccounts(accounts) {
  const previous = remoteAccounts.find((account) => account.id === selectedJobAccountID);
  remoteAccounts = accounts;
  let selected = accounts.find((account) => account.id === selectedJobAccountID);
  if (!selected && !jobAccountSearching && accounts.length) selected = accounts[0];
  if (selected) {
    if (selectedJobAccountID !== selected.id) {
      selectedJobAccountID = selected.id;
      jobAccount.value = selected.id;
      jobAccountInput.value = jobAccountDisplay(selected);
    } else if (!jobAccountSearching) {
      jobAccountInput.value = jobAccountDisplay(selected);
    }
  } else {
    selectedJobAccountID = "";
    jobAccount.value = "";
    if (!jobAccountSearching) jobAccountInput.value = "";
  }
  renderJobAccountOptions();
  if (previous?.id !== selected?.id || previous?.revision !== selected?.revision) {
    resetCourses();
    if (selected) void loadCourses();
  }
  renderAccountCards();
}

function accountJob(account) {
  return account.current_job === undefined
    ? remoteJobs.find((job) => job.account_id === account.id) || null : account.current_job;
}

function accountState(account) {
  const job = accountJob(account);
  if (account.is_studying || job?.status === "running") return "studying";
  if (["queued", "starting", "retry_wait", "stopping"].includes(job?.status)) return "queued";
  if (attentionStatuses.includes(job?.status)) return "attention";
  return "idle";
}

function accountActivity(job) {
  if (!job) return "";
  const p = job.progress || {};
  if (!activeStatuses.includes(job.status)) return "";
  if (p.phase === "video") {
    const timing = p.duration > 0 ? `${formatSeconds(p.position)} / ${formatSeconds(p.duration)}` : "";
    return [p.name, timing, videoStateLabels[p.video_state] || "播放中"].filter(Boolean).join(" · ");
  }
  if (p.phase === "workid") return quizStateLabels[p.quiz_state] || aiStateLabels[p.ai_state] || "章节测验";
  if (p.phase === "pacing") return [phaseLabels.pacing, p.name].filter(Boolean).join(" · ");
  return phaseLabels[p.phase] || p.chapter || "";
}

function createAccountView(id) {
  const item = document.createElement("li");
  item.className = "account-card";
  item.setAttribute("data-account-id", id);
  const heading = document.createElement("div");
  heading.className = "account-card-heading";
  const avatar = document.createElement("span");
  avatar.className = "account-avatar";
  const icon = document.createElement("i");
  icon.setAttribute("data-lucide", "user-round");
  avatar.append(icon);
  const identity = document.createElement("div");
  identity.className = "account-identity";
  const title = document.createElement("h2");
  const username = document.createElement("p");
  identity.append(title, username);
  const state = document.createElement("span");
  state.className = "account-state";
  heading.append(avatar, identity, state);
  const tally = document.createElement("div");
  tally.className = "account-tally";
  const count = document.createElement("strong");
  const unit = document.createElement("span");
  unit.textContent = "门";
  const label = document.createElement("span");
  label.className = "account-tally-label";
  label.textContent = "累计完成课程";
  tally.append(count, unit, label);
  const context = document.createElement("div");
  context.className = "account-context";
  const course = document.createElement("p");
  const activity = document.createElement("p");
  context.append(course, activity);
  const lastCompleted = document.createElement("p");
  lastCompleted.className = "account-last-completed";
  const actions = document.createElement("div");
  actions.className = "account-card-actions";
  const open = document.createElement("button");
  open.type = "button";
  open.className = "ghost";
  const edit = document.createElement("button");
  edit.type = "button";
  edit.className = "ghost icon-button";
  edit.setAttribute("data-account-action", "edit");
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "ghost icon-button danger";
  remove.setAttribute("data-account-action", "delete");
  if (typeof consoleActionLabel === "function") {
    consoleActionLabel(open, "book-open", "查看课程");
    consoleActionLabel(edit, "square-pen", "");
    consoleActionLabel(remove, "trash-2", "");
  } else {
    open.textContent = "查看课程";
    edit.textContent = "编辑";
    remove.textContent = "删除";
  }
  open.addEventListener("click", () => {
    jobSearch.value = "";
    openJobDialog(id);
  });
  edit.addEventListener("click", () => {
    const account = remoteAccounts.find((account) => account.id === id);
    if (account) openAccountEditor(account);
  });
  remove.addEventListener("click", () => openAccountDelete(id));
  actions.append(open, edit, remove);
  item.append(heading, tally, context, lastCompleted, actions);
  return { item, title, username, state, count, course, activity, lastCompleted, open, edit, remove };
}

function renderAccountCards() {
  const ready = backendConnected && accountsFresh;
  const ids = new Set(remoteAccounts.map((account) => account.id));
  for (const id of accountViews.keys()) if (!ids.has(id)) accountViews.delete(id);
  const search = accountSearch.value.trim().toLocaleLowerCase();
  const filtered = remoteAccounts.filter((account) => {
    const text = [account.label, account.username_masked].filter(Boolean).join(" ").toLocaleLowerCase();
    return (!search || text.includes(search))
      && (accountFilter.value === "all" || !accountFilter.value || accountState(account) === accountFilter.value);
  });
  let created = false;
  const nodes = filtered.map((account) => {
    let view = accountViews.get(account.id);
    if (!view) {
      view = createAccountView(account.id);
      accountViews.set(account.id, view);
      created = true;
    }
    const state = accountState(account);
    const job = accountJob(account);
    setText(view.title, account.label);
    setText(view.username, account.username_masked || "账号已加密");
    view.item.setAttribute("data-state", state);
    view.state.setAttribute("data-state", state);
    setText(view.state, state === "studying" ? "正在刷课"
      : state === "idle" ? "未在学习" : stateLabels[job?.status] || "待处理");
    setText(view.count, Number.isInteger(account.completed_courses)
      ? account.completed_courses.toLocaleString("zh-CN") : "--");
    setText(view.course, job?.progress?.course || (state === "idle" ? "暂无课程记录" : "等待课程信息"));
    setText(view.activity, accountActivity(job) || (state === "idle" ? "当前无进行中的学习" : stateLabels[job?.status] || ""));
    setText(view.lastCompleted, account.last_completed_at
      ? `最近完成 ${new Date(account.last_completed_at * 1000).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" })}`
      : "暂无已确认完成记录");
    view.open.disabled = view.edit.disabled = !ready;
    view.remove.disabled = !ready || account.can_delete !== true || accountDeleting;
    const reason = errorLabels[account.delete_blocked_reason] || "账号当前不可删除";
    view.remove.title = account.can_delete === true ? "删除账号" : reason;
    view.remove.setAttribute("aria-label", `删除账号 ${account.label}`);
    view.edit.title = "编辑账号";
    view.edit.setAttribute("aria-label", `编辑账号 ${account.label}`);
    return view.item;
  });
  syncChildren(accountsList, nodes);
  if (created && typeof consoleUIIcons === "function") consoleUIIcons();
  const count = (id, value) => setText(document.querySelector(`#accounts-${id}`), value);
  count("total", backendConnected ? remoteAccounts.length : "--");
  count("studying", backendConnected ? remoteAccounts.filter((account) => accountState(account) === "studying").length : "--");
  count("attention", backendConnected ? remoteAccounts.filter((account) => accountState(account) === "attention").length : "--");
  count("completed", backendConnected && remoteAccounts.every((account) => Number.isInteger(account.completed_courses))
    ? remoteAccounts.reduce((total, account) => total + account.completed_courses, 0).toLocaleString("zh-CN") : "--");
  document.querySelector("#accounts-empty").hidden = filtered.length > 0;
  setText(document.querySelector("#accounts-empty-title"), !backendConnected ? "等待连接"
    : remoteAccounts.length ? "没有匹配的账号" : "暂无账号");
  setText(document.querySelector("#accounts-status"), !backendConnected ? "等待连接"
    : !accountsFresh ? "连接中断，保留上次数据" : `显示 ${filtered.length} / ${remoteAccounts.length} 个账号`);
  if (!document.querySelector("#accounts-view").hidden) {
    setText(document.querySelector("#count"), `${remoteAccounts.length} 个账号`);
  }
  addAccountButton.disabled = !ready || accountSaving;
  accountsRefresh.disabled = !backendConnected || refreshRunning;
  accountsList.setAttribute("aria-busy", String(accountSaving || accountDeleting));
  const pending = remoteAccounts.find((account) => account.id === pendingDeleteAccountID);
  document.querySelector("#confirm-delete-account").disabled = !ready || accountDeleting || pending?.can_delete !== true;
  if (pendingDeleteAccountID && !pending && !accountDeleting) accountDeleteDialog.close();
  updateAccountDialogControls();
  updateCourseControls();
}

function updateAccountDialogControls() {
  document.querySelector("#save-account").disabled = accountSaving || !backendConnected || !accountsFresh;
  for (const id of ["account-editor-close", "account-editor-cancel"]) {
    document.querySelector(`#${id}`).disabled = accountSaving;
  }
  for (const id of ["account-delete-close", "account-delete-cancel"]) {
    document.querySelector(`#${id}`).disabled = accountDeleting;
  }
}

function closeAccountDialogs() {
  if (accountDialog.open) accountDialog.close();
  if (accountDeleteDialog.open) accountDeleteDialog.close();
  document.querySelector("#account-password").value = "";
  pendingDeleteAccountID = "";
}

function openAccountEditor(account = null) {
  if (!backendConnected || !accountsFresh || accountSaving) return;
  accountForm.reset();
  accountTarget.value = account?.id || "";
  document.querySelector("#account-label").value = account?.label || "";
  for (const name of ["username", "password"]) {
    const input = document.querySelector(`#account-${name}`);
    input.value = "";
    input.required = !account;
    input.placeholder = account ? "不修改请留空" : "";
  }
  setText(document.querySelector("#account-editor-title"), account ? "编辑账号" : "新增账号");
  document.querySelector("#account-form-error").hidden = true;
  accountDialog.showModal();
  document.querySelector("#account-label").focus();
}

function openAccountDelete(id) {
  const account = remoteAccounts.find((account) => account.id === id);
  if (!account || !backendConnected || !accountsFresh || account.can_delete !== true || accountDeleting) return;
  pendingDeleteAccountID = id;
  setText(document.querySelector("#account-delete-message"),
    `确认删除“${account.label}”（${account.username_masked || "已加密账号"}）？该账号的历史任务和累计课程记录将一并移除，共享题库不受影响。`);
  document.querySelector("#account-delete-error").hidden = true;
  accountDeleteDialog.showModal();
  renderAccountCards();
  document.querySelector("#account-delete-cancel").focus();
}

addAccountButton.addEventListener("click", () => openAccountEditor());
accountsRefresh.addEventListener("click", () => void refreshBackend());
accountSearch.addEventListener("input", renderAccountCards);
accountFilter.addEventListener("change", renderAccountCards);
for (const id of ["account-editor-close", "account-editor-cancel"]) {
  document.querySelector(`#${id}`).addEventListener("click", () => {
    if (!accountSaving) {
      document.querySelector("#account-password").value = "";
      accountDialog.close();
    }
  });
}
accountDialog.addEventListener("cancel", (event) => {
  if (accountSaving) event.preventDefault();
  else document.querySelector("#account-password").value = "";
});
accountDialog.addEventListener("close", () => {
  if (!accountDialog.open) document.querySelector("#account-password").value = "";
});
for (const id of ["account-delete-close", "account-delete-cancel"]) {
  document.querySelector(`#${id}`).addEventListener("click", () => {
    if (!accountDeleting) accountDeleteDialog.close();
  });
}
accountDeleteDialog.addEventListener("cancel", (event) => {
  if (accountDeleting) event.preventDefault();
});
accountDeleteDialog.addEventListener("close", () => {
  if (!accountDeleteDialog.open) pendingDeleteAccountID = "";
});
document.querySelector("#confirm-delete-account").addEventListener("click", async () => {
  const account = remoteAccounts.find((account) => account.id === pendingDeleteAccountID);
  if (!account || account.can_delete !== true || accountDeleting || !accountsFresh || !backendConnected) return;
  const generation = connectionGeneration;
  const error = document.querySelector("#account-delete-error");
  accountDeleting = true;
  error.hidden = true;
  updateAccountDialogControls();
  renderAccountCards();
  try {
    await backendFetch(`/accounts/${account.id}`, { method: "DELETE" });
    if (generation !== connectionGeneration) return;
    accountMutationVersion += 1;
    accountDeleteDialog.close();
    remoteJobs = remoteJobs.filter((job) => job.account_id !== account.id);
    remoteJobCount = remoteJobs.length;
    renderAccounts(remoteAccounts.filter((item) => item.id !== account.id));
    renderJobs();
    renderOverviewJobs();
    await refreshBackend();
  } catch (failure) {
    if (generation !== connectionGeneration) return;
    setText(error, failure.message || "删除账号失败");
    error.hidden = false;
  } finally {
    accountDeleting = false;
    updateAccountDialogControls();
    renderAccountCards();
  }
});

function formatSeconds(value) {
  const seconds = Math.max(0, Math.floor(Number(value) || 0));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const rest = seconds % 60;
  if (hours) return `${hours}:${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`;
  return `${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`;
}

function appendJobDetail(container, label, value, className = "") {
  const row = document.createElement("div");
  row.className = "job-detail";
  const key = document.createElement("span");
  key.className = "job-detail-label";
  key.textContent = label;
  const content = document.createElement("span");
  content.className = `job-detail-value${className ? ` ${className}` : ""}`;
  if (typeof value === "string") content.textContent = value;
  else if (value) content.append(value);
  row.append(key, content);
  container.append(row);
  return { row, content };
}

function setText(element, value) {
  const text = String(value ?? "");
  if (element.textContent !== text) element.textContent = text;
}

function syncChildren(parent, children) {
  const wanted = new Set(children);
  for (const child of [...parent.children]) {
    if (!wanted.has(child)) child.remove();
  }
  children.forEach((child, index) => {
    if (parent.children[index] !== child) parent.insertBefore(child, parent.children[index] || null);
  });
}

function canMutateJobs() {
  return backendConnected && accountsFresh && !jobBatchSubmitting && !jobSubmitting && !singleJobActions.size;
}

function findJob(id) {
  return remoteJobs.find((job) => job.id === id) || archivedJobs.find((job) => job.id === id);
}

function jobMatchesSearch(job, query) {
  const account = remoteAccounts.find((item) => item.id === job.account_id);
  return !query || [job.label, account?.label, account?.username_masked,
    job.progress?.course, job.progress?.chapter, ...(job.course_ids || [])]
    .filter(Boolean).join(" ").toLocaleLowerCase().includes(query);
}

function jobCourseOptions() {
  const courses = new Map();
  for (const job of remoteJobs) {
    for (const value of job.course_ids || []) {
      const id = String(value);
      let course = courses.get(id);
      if (!course) {
        course = { id, title: "", accounts: new Set() };
        courses.set(id, course);
      }
      if (job.account_id) course.accounts.add(job.account_id);
      if (!course.title && String(job.progress?.course_id) === id && job.progress?.course) {
        course.title = job.progress.course;
      }
    }
  }
  for (const job of remoteJobs) {
    const catalog = liveCourseCatalogs.get(job.account_id)?.result;
    for (const item of catalog?.courses || []) {
      const course = courses.get(String(item.id));
      if (course && !course.title && item.title) course.title = item.title;
    }
  }
  return [...courses.values()]
    .map((course) => ({ ...course, title: course.title || `课程 ${course.id}` }))
    .sort((a, b) => a.title.localeCompare(b.title, "zh-CN", { numeric: true, sensitivity: "base" })
      || a.id.localeCompare(b.id));
}

function renderJobCourseFilter() {
  const courses = jobCourseOptions();
  const selected = jobCourseFilter.value;
  const signature = JSON.stringify(courses.map((course) => [
    course.id, course.title, course.accounts.size,
  ]));
  if (signature === jobCourseFilterSignature) return;
  if (selected && !courses.some((course) => course.id === selected)) {
    courses.push({ id: selected, title: `课程 ${selected}`, accounts: new Set() });
  }
  jobCourseFilterSignature = signature;
  jobCourseFilter.replaceChildren(...[{ id: "", title: "全部课程" }, ...courses].map((course) => {
    const option = document.createElement("option");
    option.value = course.id;
    option.textContent = course.id
      ? `${course.title} (${course.accounts.size} 个账号)` : "全部课程";
    return option;
  }));
  jobCourseFilter.value = selected;
}

function fixedJobOrder(jobs) {
  return [...jobs].sort((a, b) => {
    return (Number(b.created_at) || 0) - (Number(a.created_at) || 0);
  });
}

function visibleJobs() {
  const search = jobSearch.value.trim().toLocaleLowerCase();
  const courseID = jobCourseFilter.value;
  return fixedJobOrder(remoteJobs.filter((job) => {
    if (job.archived_at) return false;
    const matchesCourse = !courseID
      || (job.course_ids || []).some((id) => String(id) === courseID);
    const matches = jobFilter === "all"
      || (jobFilter === "pacing" && pacingWaitJob(job))
      || (jobFilter === "active" && activeStatuses.includes(job.status) && !pacingWaitJob(job))
      || (jobFilter === "attention" && attentionStatuses.includes(job.status))
      || (jobFilter === "finished" && finishedStatuses.includes(job.status));
    return matchesCourse && matches && jobMatchesSearch(job, search);
  }));
}

function jobBatchEligible(job, action = jobBatchAction.value) {
  if (!job || !jobBatchActions[action]?.statuses.includes(job.status)) return false;
  if (action === "archive") return !job.archived_at;
  if (action === "unarchive") return !!job.archived_at;
  return !job.archived_at;
}

function jobBatchTargets() {
  return remoteJobs.filter((job) => selectedJobIDs.has(job.id) && jobBatchEligible(job));
}

function setJobSelected(id, selected) {
  if (selected) selectedJobIDs.add(id);
  else selectedJobIDs.delete(id);
  renderJobSelection();
}

function resetJobBatch() {
  selectedJobIDs.clear();
  singleJobActions.clear();
  jobBatchSubmitting = false;
  jobBatchConfirmationKey = "";
  jobBatchConfirm.checked = false;
  jobBatchResults.clear();
  jobBatchViews.clear();
  jobBatchList.replaceChildren();
  document.querySelector("#job-batch-error").hidden = true;
  document.querySelector("#job-batch-result").hidden = true;
  if (jobBatchDialog.open) jobBatchDialog.close();
}

function resetArchivedJobs() {
  archivedRequestGeneration += 1;
  archivedLoading = false;
  archivedJobs = [];
  archivedLastRefreshAt = 0;
  archivedAccountViews.clear();
  setText(document.querySelector("#archived-error"), "");
  archivedRefresh.disabled = !backendConnected;
  renderArchivedJobs();
}

function createJobBatchRow(id) {
  const item = document.createElement("li");
  item.className = "job-batch-row";
  item.setAttribute("data-job-id", id);
  const label = document.createElement("label");
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.addEventListener("change", () => {
    if (canMutateJobs() && jobBatchEligible(remoteJobs.find((job) => job.id === id))) {
      setJobSelected(id, checkbox.checked);
    } else renderJobSelection();
  });
  const identity = document.createElement("span");
  identity.className = "job-batch-identity";
  const title = document.createElement("strong");
  const context = document.createElement("span");
  identity.append(title, context);
  const state = document.createElement("span");
  label.append(checkbox, identity, state);
  const result = document.createElement("span");
  result.className = "job-batch-row-result";
  item.append(label, result);
  return { item, checkbox, title, context, state, result };
}

function renderJobSelection() {
  const ready = canMutateJobs();
  const visible = visibleJobs();
  const selectedVisible = visible.filter((job) => selectedJobIDs.has(job.id)).length;
  setText(document.querySelector("#job-selection-count"), `已选 ${selectedJobIDs.size} 项 · 当前 ${visible.length} 项`);
  selectVisibleJobs.checked = visible.length > 0 && selectedVisible === visible.length;
  selectVisibleJobs.indeterminate = selectedVisible > 0 && selectedVisible < visible.length;
  selectVisibleJobs.disabled = !ready || !visible.length;
  jobBatchButton.disabled = !ready || !remoteJobs.length;
  document.querySelector("#clear-job-selection").disabled = !ready || !selectedJobIDs.size;
  for (const action of ["resume", "pause"]) {
    document.querySelector(`#quick-${action}-jobs`).disabled = !ready
      || !remoteJobs.some((job) => selectedJobIDs.has(job.id) && jobBatchEligible(job, action));
  }
  for (const [id, view] of jobViews) {
    view.selection.checked = selectedJobIDs.has(id);
    const job = findJob(id);
    view.selection.disabled = !ready || !job || !!job.archived_at;
    view.item.setAttribute("data-selected", String(view.selection.checked));
    for (const button of view.actions.children) button.disabled = !ready;
  }
  if (!jobBatchDialog.open) return;
  const config = jobBatchActions[jobBatchAction.value];
  const targets = jobBatchTargets();
  const confirmationKey = `${jobBatchAction.value}:${targets.map((job) => job.id).sort().join(",")}`;
  if (jobBatchConfirmationKey !== confirmationKey || !accountsFresh) {
    jobBatchConfirm.checked = false;
    jobBatchConfirmationKey = confirmationKey;
  }
  const search = jobBatchSearch.value.trim().toLocaleLowerCase();
  const matches = fixedJobOrder(remoteJobs.filter((job) => jobMatchesSearch(job, search)));
  const eligible = matches.filter((job) => jobBatchEligible(job));
  const selectedEligible = eligible.filter((job) => selectedJobIDs.has(job.id)).length;
  const ids = new Set(remoteJobs.map((job) => job.id));
  for (const id of jobBatchViews.keys()) if (!ids.has(id)) jobBatchViews.delete(id);
  const rows = matches.map((job) => {
    let view = jobBatchViews.get(job.id);
    if (!view) {
      view = createJobBatchRow(job.id);
      jobBatchViews.set(job.id, view);
    }
    const account = remoteAccounts.find((item) => item.id === job.account_id);
    setText(view.title, job.label || account?.label || "课程任务");
    setText(view.context, [account?.username_masked,
      job.progress?.course || `课程 ID：${(job.course_ids || []).join("、")}`].filter(Boolean).join(" · "));
    const available = jobBatchEligible(job);
    view.checkbox.checked = selectedJobIDs.has(job.id);
    view.checkbox.disabled = !ready || !available;
    view.checkbox.setAttribute("aria-label", `选择任务：${job.label || job.id}`);
    view.item.setAttribute("data-selected", String(view.checkbox.checked));
    view.item.setAttribute("data-eligible", String(available));
    view.state.className = `job-state state-${job.status}`;
    setText(view.state, stateLabels[job.status] || job.status);
    const result = jobBatchResults.get(job.id);
    view.result.hidden = !result;
    view.result.setAttribute("data-ok", String(!!result?.ok));
    setText(view.result, result?.message);
    return view.item;
  });
  syncChildren(jobBatchList, rows);
  document.querySelector("#job-batch-empty").hidden = matches.length > 0;
  jobBatchForm.setAttribute("aria-busy", String(jobBatchSubmitting));
  jobBatchAction.disabled = jobBatchSearch.disabled = !ready;
  jobBatchSelectAll.disabled = !ready || !eligible.length;
  jobBatchSelectAll.checked = eligible.length > 0 && selectedEligible === eligible.length;
  jobBatchSelectAll.indeterminate = selectedEligible > 0 && selectedEligible < eligible.length;
  document.querySelector("#job-batch-clear").disabled = !ready || !selectedJobIDs.size;
  setText(document.querySelector("#job-batch-count"),
    `已选 ${selectedJobIDs.size} · 可${config.label} ${targets.length} · 不适用 ${selectedJobIDs.size - targets.length}`);
  setText(document.querySelector("#job-batch-note"), !backendConnected || !accountsFresh
    ? "连接不可用，等待同步任务状态。" : config.note);
  document.querySelector("#job-batch-confirm-label").hidden = !config.destructive;
  jobBatchConfirm.disabled = !ready || !targets.length;
  setText(document.querySelector("#job-batch-confirm-text"), config.destructive
    ? `确认${config.label}这 ${targets.length} 项任务${jobBatchAction.value === "delete" ? "记录，此操作不可撤销" : ""}` : "");
  const submit = document.querySelector("#job-batch-submit");
  submit.disabled = !ready || !targets.length || targets.length > 1000
    || (!!config.destructive && !jobBatchConfirm.checked);
  submit.className = config.destructive ? "danger" : "";
  const label = jobBatchSubmitting ? "处理中…" : `${config.label} ${targets.length} 项`;
  if (submit.dataset.action !== jobBatchAction.value && typeof consoleActionLabel === "function") {
    consoleActionLabel(submit, config.icon, label);
    // The icon helper replaces children, so retain the label's stable selector.
    submit.querySelector("span").id = "job-batch-submit-label";
  }
  submit.dataset.action = jobBatchAction.value;
  setText(document.querySelector("#job-batch-submit-label"), label);
  for (const id of ["job-batch-close", "job-batch-cancel"]) document.querySelector(`#${id}`).disabled = jobBatchSubmitting;
}

function openJobBatch(action = "") {
  if (!canMutateJobs() || !remoteJobs.length) return;
  jobBatchAction.value = action || ["resume", "pause", "retry", "cancel"].find(
    (key) => remoteJobs.some((job) => selectedJobIDs.has(job.id) && jobBatchEligible(job, key)),
  ) || "resume";
  jobBatchSearch.value = "";
  jobBatchConfirm.checked = false;
  jobBatchConfirmationKey = "";
  jobBatchResults.clear();
  document.querySelector("#job-batch-error").hidden = true;
  document.querySelector("#job-batch-result").hidden = true;
  if (!jobBatchDialog.open) jobBatchDialog.showModal();
  renderJobSelection();
  jobBatchAction.focus();
}

function closeJobBatch() {
  if (!jobBatchSubmitting && jobBatchDialog.open) jobBatchDialog.close();
}

jobBatchButton.addEventListener("click", () => openJobBatch());
for (const action of ["resume", "pause"]) {
  document.querySelector(`#quick-${action}-jobs`).addEventListener("click", () => openJobBatch(action));
}
for (const id of ["job-batch-close", "job-batch-cancel"]) {
  document.querySelector(`#${id}`).addEventListener("click", closeJobBatch);
}
jobBatchDialog.addEventListener("cancel", (event) => {
  if (jobBatchSubmitting) event.preventDefault();
});
jobBatchAction.addEventListener("change", () => {
  jobBatchResults.clear();
  jobBatchConfirm.checked = false;
  document.querySelector("#job-batch-error").hidden = true;
  document.querySelector("#job-batch-result").hidden = true;
  renderJobSelection();
});
jobBatchSearch.addEventListener("input", renderJobSelection);
jobBatchConfirm.addEventListener("change", renderJobSelection);
selectVisibleJobs.addEventListener("change", () => {
  if (canMutateJobs()) {
    for (const job of visibleJobs()) {
      if (selectVisibleJobs.checked) selectedJobIDs.add(job.id);
      else selectedJobIDs.delete(job.id);
    }
  }
  renderJobSelection();
});
jobBatchSelectAll.addEventListener("change", () => {
  if (canMutateJobs()) {
    const search = jobBatchSearch.value.trim().toLocaleLowerCase();
    for (const job of remoteJobs.filter((item) => jobMatchesSearch(item, search) && jobBatchEligible(item))) {
      if (jobBatchSelectAll.checked) selectedJobIDs.add(job.id);
      else selectedJobIDs.delete(job.id);
    }
  }
  renderJobSelection();
});
for (const id of ["clear-job-selection", "job-batch-clear"]) {
  document.querySelector(`#${id}`).addEventListener("click", () => {
    if (canMutateJobs()) selectedJobIDs.clear();
    renderJobSelection();
  });
}

jobBatchForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!jobBatchDialog.open || !canMutateJobs()) return;
  const action = jobBatchAction.value;
  const config = jobBatchActions[action];
  const targets = jobBatchTargets().map((job) => job.id);
  if (!targets.length || targets.length > 1000 || (config.destructive && !jobBatchConfirm.checked)) return;
  const generation = connectionGeneration;
  const error = document.querySelector("#job-batch-error");
  const summary = document.querySelector("#job-batch-result");
  jobBatchSubmitting = true;
  jobBatchResults.clear();
  error.hidden = summary.hidden = true;
  renderJobSelection();
  updateCourseControls();
  try {
    const response = await backendFetch("/jobs/batch", {
      method: "POST", body: JSON.stringify({ action, job_ids: targets }),
    });
    if (generation !== connectionGeneration) return;
    const results = response.results;
    if (response.action !== action || !Array.isArray(results) || results.length !== targets.length
        || new Set(results.map((item) => item?.id)).size !== targets.length
        || results.some((item) => !item || !targets.includes(item.id) || typeof item.ok !== "boolean"
          || (item.ok && (action === "delete" ? item.deleted !== true : item.job?.id !== item.id)))) {
      throw new Error("批量响应不完整");
    }
    for (const result of results) {
      if (result.ok) {
        selectedJobIDs.delete(result.id);
        if (action === "delete") {
          remoteJobs = remoteJobs.filter((job) => job.id !== result.id);
          archivedJobs = archivedJobs.filter((job) => job.id !== result.id);
        } else if (action === "archive") {
          remoteJobs = remoteJobs.filter((job) => job.id !== result.id);
          archivedJobs = fixedJobOrder(
            [result.job, ...archivedJobs.filter((job) => job.id !== result.id)],
          );
        } else {
          remoteJobs = remoteJobs.map((job) => job.id === result.id ? result.job : job);
        }
      }
      jobBatchResults.set(result.id, {
        ok: result.ok,
        message: result.ok ? `${config.label}成功`
          : errorLabels[result.error] || (result.code === "NOT_FOUND" ? "任务已不存在" : result.error || "操作失败"),
      });
    }
    const succeeded = results.filter((result) => result.ok).length;
    setText(summary, `${config.label}完成：成功 ${succeeded} 项 · 失败 ${targets.length - succeeded} 项`);
    summary.dataset.failed = String(succeeded < targets.length);
    summary.hidden = false;
  } catch (failure) {
    if (generation !== connectionGeneration) return;
    setText(error, `${failure.message || "批量请求失败"}。部分操作可能已生效，请核对状态后再操作。`);
    error.hidden = false;
  } finally {
    if (generation === connectionGeneration) {
      jobMutationVersion += 1;
      accountsFresh = false;
      jobBatchSubmitting = false;
      jobBatchConfirm.checked = false;
      remoteJobCount = remoteJobs.length;
      renderJobs();
      renderArchivedJobs();
      renderOverviewJobs();
      renderAccountCards();
      updateCourseControls();
      void refreshBackend();
    }
  }
});

async function actOnJob(id, action) {
  if (!canMutateJobs()) return;
  if (action === "delete" && typeof confirm === "function" && !confirm("删除这条任务记录？")) return;
  const generation = connectionGeneration;
  singleJobActions.add(id);
  renderJobSelection();
  updateCourseControls();
  backendError.hidden = true;
  try {
    const deleting = action === "delete";
    const result = await backendFetch(deleting ? `/jobs/${id}` : `/jobs/${id}/${action}`, {
      method: deleting ? "DELETE" : "POST", body: deleting ? undefined : "{}",
    });
    if (generation !== connectionGeneration) return;
    if (deleting) {
      remoteJobs = remoteJobs.filter((job) => job.id !== id);
      archivedJobs = archivedJobs.filter((job) => job.id !== id);
    } else if (action === "archive") {
      remoteJobs = remoteJobs.filter((job) => job.id !== id);
      archivedJobs = fixedJobOrder([result, ...archivedJobs.filter((job) => job.id !== id)]);
    } else if (action === "unarchive") {
      archivedJobs = archivedJobs.filter((job) => job.id !== id);
      remoteJobs = fixedJobOrder([result, ...remoteJobs.filter((job) => job.id !== id)]);
    } else {
      remoteJobs = remoteJobs.map((job) => job.id === id ? result : job);
    }
  } catch (error) {
    if (generation !== connectionGeneration) return;
    setText(backendError, error.message || "请求失败");
    backendError.hidden = false;
  } finally {
    if (generation === connectionGeneration) {
      singleJobActions.delete(id);
      jobMutationVersion += 1;
      accountsFresh = false;
      remoteJobCount = remoteJobs.length;
      renderJobs();
      renderArchivedJobs();
      renderOverviewJobs();
      renderAccountCards();
      updateCourseControls();
      void refreshBackend();
    }
  }
}

function createJobView() {
  const item = document.createElement("li");
  item.className = "job";
  const heading = document.createElement("div");
  heading.className = "job-heading";
  const avatar = document.createElement("span");
  avatar.className = "job-avatar";
  const icon = document.createElement("i");
  icon.setAttribute("data-lucide", "book-open-check");
  avatar.append(icon);
  const title = document.createElement("h2");
  const state = document.createElement("span");
  const select = document.createElement("label");
  select.className = "job-selection-toggle";
  select.title = "选择任务";
  const selection = document.createElement("input");
  selection.type = "checkbox";
  selection.addEventListener("change", () => {
    if (canMutateJobs()) setJobSelected(item.getAttribute("data-job-id"), selection.checked);
    else renderJobSelection();
  });
  select.append(selection);
  heading.append(avatar, title, state, select);
  const detail = document.createElement("p");
  detail.className = "job-context";
  const details = document.createElement("div");
  details.className = "job-details";
  const rows = {};
  for (const [key, label, className] of [
    ["course", "课程"], ["chapter", "章节"], ["phase", "当前阶段"],
    ["failedCourses", "未完成课程"], ["videoName", "当前视频"], ["documentName", "当前文件"],
    ["video", "视频进度", "job-video-value"], ["videoState", "任务点确认"],
    ["videoError", "媒体状态", "error"], ["ai", "题库状态"],
    ["aiType", "题型"], ["aiQuestion", "当前题目", "job-question"],
    ["aiError", "查询结果", "error"], ["quizState", "答题状态"], ["quizAnswer", "本题答案"],
  ]) rows[key] = appendJobDetail(details, label, "", className);
  const videoText = document.createElement("span");
  const videoProgress = document.createElement("progress");
  videoProgress.setAttribute("aria-label", "视频进度");
  rows.video.content.append(videoText, videoProgress);
  const aiState = document.createElement("span");
  rows.ai.content.append(aiState);

  const visual = document.createElement("section");
  visual.className = "job-progress-visual";
  visual.hidden = true;
  const visualMain = document.createElement("div");
  visualMain.className = "job-progress-main";
  const visualEyebrow = document.createElement("span");
  visualEyebrow.className = "job-progress-eyebrow";
  visualEyebrow.textContent = "正在看课";
  const visualVideo = document.createElement("strong");
  visualVideo.className = "job-progress-video";
  const visualContext = document.createElement("span");
  visualContext.className = "job-progress-context";
  const visualStats = document.createElement("div");
  visualStats.className = "job-progress-stats";
  const visualPercent = document.createElement("strong");
  visualPercent.className = "job-progress-percent";
  const visualTime = document.createElement("span");
  visualTime.className = "job-progress-time";
  const visualTrack = document.createElement("progress");
  visualTrack.setAttribute("aria-label", "当前视频播放进度");
  visualStats.append(visualPercent, visualTime);
  visualMain.append(visualEyebrow, visualVideo, visualContext, visualStats, visualTrack);
  const overall = document.createElement("div");
  overall.className = "job-progress-overall";
  overall.setAttribute("role", "progressbar");
  overall.setAttribute("aria-label", "课程任务点完成度");
  overall.setAttribute("aria-valuemin", "0");
  overall.setAttribute("aria-valuemax", "100");
  const overallPercent = document.createElement("strong");
  const overallLabel = document.createElement("span");
  overallLabel.textContent = "课程完成";
  overall.append(overallPercent, overallLabel);
  visual.append(visualMain, overall);

  const manualReview = document.createElement("section");
  manualReview.className = "job-manual-review";
  const manualHeading = document.createElement("h3");
  const manualList = document.createElement("ul");
  manualReview.append(manualHeading, manualList);

  const progress = document.createElement("progress");
  progress.setAttribute("aria-label", "章节进度");
  const meta = document.createElement("div");
  meta.className = "meta";
  const completed = document.createElement("span");
  const time = document.createElement("time");
  meta.append(completed, time);
  const courses = createJobCourses();
  const error = document.createElement("p");
  error.className = "error";
  const actions = document.createElement("div");
  actions.className = "actions";
  item.append(heading, detail, details, visual, manualReview, progress, meta, courses.panel, error, actions);
  return {
    item, title, state, detail, rows, videoText, videoProgress, aiState, visual,
    visualVideo, visualContext, visualPercent, visualTime, visualTrack, overall,
    overallPercent, progress,
    completed, time, error, actions, actionKey: "", courses, selection, selectionToggle: select,
    manualReview, manualHeading, manualList, manualTaskKey: "",
  };
}

function updateJobActions(view, job) {
  const available = [];
  if (job.archived_at) {
    available.push(["unarchive", "恢复"]);
  } else if (["queued", "starting", "running", "retry_wait"].includes(job.status)) {
    available.push(["pause", "暂停"], ["cancel", "取消"]);
  } else if (job.status === "paused") {
    available.push(["resume", "继续"], ["cancel", "取消"]);
  } else if (["succeeded", "failed", "cancelled", "needs_attention"].includes(job.status)) {
    available.push(["retry", "重试"]);
    if (["succeeded", "failed", "cancelled"].includes(job.status)) available.push(["archive", "归档"]);
    available.push(["delete", "删除"]);
    if (job.status === "needs_attention") available.push(["cancel", "取消"]);
  }
  const actionKey = `${job.id}:${job.archived_at ? "archived" : "visible"}:${available.map(([action]) => action).join(",")}`;
  if (view.actionKey === actionKey) return;
  view.actionKey = actionKey;
  const buttons = available.map(([action, label]) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "ghost";
    button.setAttribute("data-action", action);
    button.textContent = label;
    if (typeof consoleActionLabel === "function") {
      consoleActionLabel(button, {
        pause: "pause", cancel: "square", resume: "play", retry: "rotate-cw",
        archive: "archive", unarchive: "archive-restore", delete: "trash-2",
      }[action], label);
    }
    button.addEventListener("click", () => void actOnJob(job.id, action));
    return button;
  });
  view.actions.replaceChildren(...buttons);
  view.actions.hidden = !buttons.length;
  if (typeof consoleUIIcons === "function") consoleUIIcons();
}

function renderJob(job, registry = jobViews, archived = false) {
  let view = registry.get(job.id);
  if (!view) {
    view = createJobView();
    registry.set(job.id, view);
  }
  const p = job.progress || {};
  view.item.setAttribute("data-status", job.status);
  view.item.setAttribute("data-job-id", job.id);
  view.item.setAttribute("data-selected", String(selectedJobIDs.has(job.id)));
  view.selectionToggle.hidden = archived;
  view.selection.checked = selectedJobIDs.has(job.id);
  view.selection.disabled = archived || !canMutateJobs() || !!job.archived_at;
  view.selection.setAttribute("aria-label", `选择任务：${job.label || job.id}`);
  const row = (key, value, visible = !!value) => {
    view.rows[key].row.hidden = !visible;
    if (key !== "video" && key !== "ai") setText(view.rows[key].content, value);
  };
  setText(view.title, job.label);
  view.state.className = `job-state state-${job.status}`;
  setText(view.state, stateLabels[job.status] || job.status);
  const watchOnly = Boolean(job.options?.videos_only);
  setText(view.detail, [p.course, p.chapter, phaseLabels[p.phase] || "", watchOnly ? "看课模式" : ""].filter(Boolean).join(" · ")
    || `课程 ID：${(job.course_ids || []).join("、")}`);
  row("course", p.course, !watchOnly);
  row("chapter", p.chapter, !watchOnly);
  row("phase", [phaseLabels[p.phase] || p.phase, p.phase === "pacing" ? p.name : ""]
    .filter(Boolean).join(" · "));
  row("failedCourses", p.failed_courses);
  const video = p.phase === "video";
  const documentTask = p.phase === "document";
  const duration = Math.max(0, Math.floor(Number(p.duration) || 0));
  const position = Math.min(duration || Number.MAX_SAFE_INTEGER, Math.max(0, Math.floor(Number(p.position) || 0)));
  row("videoName", p.name, video && !!p.name && !watchOnly);
  row("documentName", p.name, documentTask && !!p.name);
  row("video", "", video && duration > 0 && !watchOnly);
  setText(view.videoText, `${formatSeconds(position)} / ${formatSeconds(duration)}`);
  view.videoProgress.max = Math.max(1, duration);
  view.videoProgress.value = position;
  const watchVisual = watchOnly && video && duration > 0;
  view.visual.hidden = !watchVisual;
  const videoPercent = watchVisual ? Math.min(100, position / duration * 100) : 0;
  const total = Math.max(0, Math.floor(Number(p.total) || 0));
  const completed = Math.max(0, Math.min(total, Math.floor(Number(p.completed) || 0)));
  const overallPercentValue = total ? Math.round(completed / total * 100) : 0;
  setText(view.visualVideo, p.name || "当前视频");
  setText(view.visualContext, [p.course, p.chapter].filter(Boolean).join(" · ") || "正在处理课程视频");
  setText(view.visualPercent, `${videoPercent.toFixed(1)}%`);
  setText(view.visualTime, `${formatSeconds(position)} / ${formatSeconds(duration)}`);
  view.visualTrack.max = Math.max(1, duration);
  view.visualTrack.value = position;
  view.overall.setAttribute("aria-valuenow", String(overallPercentValue));
  view.overall.setAttribute("aria-valuetext", total
    ? `${completed} / ${total} 个任务点已完成` : "暂无课程完成统计");
  view.overall.setAttribute("style", `--progress: ${overallPercentValue}%`);
  setText(view.overallPercent, total ? `${overallPercentValue}%` : "--");
  const rollbackCount = Math.max(0, Math.floor(Number(p.rollback_count) || 0));
  row("videoState", [
    videoStateLabels[p.video_state] || (duration && position >= duration
      ? videoStateLabels.confirming : videoStateLabels.playing),
    rollbackCount ? `平台已回退 ${rollbackCount} 次` : "",
  ].filter(Boolean).join(" · "), video && !watchOnly);
  row("videoError", p.video_error, video && !!p.video_error);
  const quiz = p.phase === "workid";
  row("ai", "", quiz && !!(p.ai_state || p.ai_question || p.ai_error));
  view.aiState.className = `ai-state ai-${String(p.ai_state || "unknown").replace(/[^a-z_]/g, "")}`;
  setText(view.aiState, [
    aiStateLabels[p.ai_state] || p.ai_state || "等待查询",
    p.ai_mode === "vision" ? `${Math.max(0, Number(p.ai_image_count) || 0)} 张图片` : "",
  ].filter(Boolean).join(" · "));
  row("aiType", questionTypeLabels[p.ai_type] || p.ai_type, quiz && !!p.ai_type);
  row("aiQuestion", p.ai_question, quiz && !!p.ai_question);
  row("aiError", p.ai_error, quiz && !!p.ai_error);
  row("quizState", quizStateLabels[p.quiz_state] || p.quiz_state, quiz && !!p.quiz_state);
  row("quizAnswer", p.quiz_answer, quiz && !!p.quiz_answer);
  const manualTasks = manualTasksFor(job).slice(0, 40);
  const manualTaskKey = JSON.stringify(manualTasks);
  view.manualReview.hidden = manualTasks.length === 0;
  if (view.manualTaskKey !== manualTaskKey) {
    view.manualTaskKey = manualTaskKey;
    setText(view.manualHeading, `待人工查看 ${manualTasks.length} 项`);
    const labels = { hyperlink: "超链接", document: "文件", read: "阅读", workid: "测验", book: "图书（需手动阅读）" };
    const nodes = manualTasks.map((task) => {
      const item = document.createElement("li");
      const title = document.createElement("span");
      title.className = "job-manual-task-title";
      setText(title, task.title.slice(0, 200));
      const meta = document.createElement("span");
      meta.className = "job-manual-task-meta";
      const card = /^\d{1,6}$/.test(String(task.card_num || "")) ? `任务卡 ${task.card_num}` : "任务卡";
      const location = task.chapter || task.course || "";
      setText(meta, `${location ? `${location} · ` : ""}${card} · ${labels[task.type] || String(task.type || "待确认").slice(0, 40)}`);
      item.append(title, meta);
      return item;
    });
    view.manualList.replaceChildren(...nodes);
  }
  view.progress.max = Math.max(1, total);
  view.progress.value = Math.min(view.progress.max, completed);
  setText(view.completed, `${completed} / ${total} 章节`);
  setText(view.time, new Date(job.updated_at * 1000).toLocaleString());
  view.error.hidden = !job.error_code;
  setText(view.error, errorLabels[job.error_code] || job.error_code);
  updateJobActions(view, job);
  for (const button of view.actions.children) button.disabled = !canMutateJobs();
  updateJobCourses(view.courses, job);
  return view.item;
}

function createJobCourses() {
  const panel = document.createElement("section");
  panel.className = "course-detail-panel job-courses";
  const heading = document.createElement("div");
  heading.className = "job-courses-heading";
  const title = document.createElement("h3");
  title.textContent = "课程列表";
  const count = document.createElement("span");
  const list = document.createElement("div");
  list.className = "job-course-list";
  heading.append(title, count);
  panel.append(heading, list);
  return { panel, count, list, entries: new Map() };
}

function jobCourseData(job, courseID) {
  const catalog = liveCourseCatalogs.get(job.account_id)?.result;
  const resource = liveCourseDetails.get(`${job.account_id}/${courseID}`);
  const course = catalog?.courses?.find((item) => String(item.id) === courseID);
  const detail = resource?.result?.detail;
  const detailSummary = detail?.totals ? { status: "ready", ...detail.totals,
    locked_count: (detail.points || []).filter((point) => point.locked).length } : null;
  const summary = detailSummary && (!course?.summary
    || Number(resource.result.updated_at || 0) >= Number(catalog.updated_at || 0))
    ? detailSummary : course?.summary;
  return {
    title: course?.title || detail?.course?.title
      || (String(job.progress?.course_id) === courseID ? job.progress?.course : "") || `课程 ${courseID}`,
    summary, resource,
  };
}

function jobCourseState(job, courseID, summary) {
  const current = String(job.progress?.course_id) === courseID;
  if (current && activeStatuses.includes(job.status)) {
    return {
      running: ["running", "正在学习"], starting: ["starting", "准备学习"],
      queued: ["queued", "排队中"], retry_wait: ["retry_wait", "等待重试"],
      stopping: ["stopping", "停止中"],
    }[job.status];
  }
  if (summary?.status === "ready" && Number.isInteger(summary.remaining_count)) {
    if (summary.task_count > 0 && summary.remaining_count === 0) return ["succeeded", "已完成"];
    if (summary.task_count === 0) return ["unknown", "无任务点"];
  }
  if (current && ["paused", "failed", "needs_attention", "cancelled"].includes(job.status)) {
    return [job.status, stateLabels[job.status] || job.status];
  }
  return summary?.status === "ready" && Number.isInteger(summary.remaining_count)
    ? ["queued", job.status === "succeeded" ? "仍有剩余" : "待学习"] : ["unknown", "待确认"];
}

function createJobCourse(courseID) {
  const group = document.createElement("details");
  group.className = "job-course";
  group.setAttribute("data-course-id", courseID);
  const header = document.createElement("summary");
  header.className = "job-course-header";
  const number = document.createElement("span");
  number.className = "job-course-number";
  const identity = document.createElement("span");
  identity.className = "job-course-identity";
  const title = document.createElement("strong");
  const totals = document.createElement("span");
  const progress = document.createElement("progress");
  progress.setAttribute("aria-label", "课程任务点进度");
  identity.append(title, totals, progress);
  const state = document.createElement("span");
  const chevron = document.createElement("i");
  chevron.setAttribute("data-lucide", "chevron-down");
  chevron.setAttribute("aria-hidden", "true");
  header.append(number, identity, state, chevron);
  const outline = createLiveOutline();
  group.append(header, outline.panel);
  const entry = { group, header, number, title, totals, progress, state, outline, job: null };
  group.addEventListener("toggle", () => {
    if (!group.open || !entry.job || !backendConnected || !group.isConnected) return;
    void refreshLiveCourse(entry.job, courseID);
    updateLiveOutline(outline, entry.job, courseID);
  });
  return entry;
}

function updateJobCourses(view, job) {
  const courseIDs = [...new Set((job.course_ids || []).map(String))];
  view.panel.hidden = !courseIDs.length;
  const currentID = String(job.progress?.course_id || "");
  const nodes = [];
  let completed = 0;
  let added = false;
  for (const [index, courseID] of courseIDs.entries()) {
    let entry = view.entries.get(courseID);
    if (!entry) {
      entry = createJobCourse(courseID);
      view.entries.set(courseID, entry);
      added = true;
    }
    entry.job = job;
    const { title, summary } = jobCourseData(job, courseID);
    const [status, label] = jobCourseState(job, courseID, summary);
    if (status === "succeeded") completed += 1;
    entry.group.setAttribute("data-current", String(courseID === currentID));
    entry.group.setAttribute("data-status", status);
    entry.state.className = `job-state state-${status}`;
    setText(entry.state, label);
    setText(entry.number, String(index + 1).padStart(2, "0"));
    setText(entry.title, title);
    const ready = summary?.status === "ready" && Number.isInteger(summary.remaining_count);
    const picked = job.options?.point_ids?.[courseID]?.length || 0;
    setText(entry.totals, ready
      ? `任务点 ${summary.task_count || 0} · 已完成 ${summary.completed_count || 0} · 剩余 ${summary.remaining_count}${summary.locked_count ? ` · 锁定 ${summary.locked_count}` : ""}${picked ? ` · 只学 ${picked} 章` : ""}`
      : summary ? "任务点统计读取失败" : "暂无任务点统计");
    entry.progress.hidden = !ready || !(summary.task_count > 0);
    entry.progress.max = Math.max(1, summary?.task_count || 0);
    entry.progress.value = Math.max(0, Math.min(entry.progress.max, summary?.completed_count || 0));
    updateLiveOutline(entry.outline, job, courseID);
    nodes.push(entry.group);
  }
  setText(view.count, `共 ${courseIDs.length} 门 · 已完成 ${completed} 门`);
  for (const courseID of view.entries.keys()) if (!courseIDs.includes(courseID)) view.entries.delete(courseID);
  syncChildren(view.list, nodes);
  if (added && typeof consoleUIIcons === "function") consoleUIIcons();
}

function createLiveOutline() {
  const panel = document.createElement("div");
  panel.className = "live-course-panel";
  const status = document.createElement("p");
  status.className = "connection-status";
  const list = document.createElement("div");
  list.className = "course-detail-list";
  panel.append(status, list);
  return { panel, status, list, rows: new Map(), current: null, currentKey: "", scrolledKey: "",
    detail: null, chapterKey: "" };
}

function updateLiveOutline(view, job, courseID) {
  const resource = liveCourseDetails.get(`${job.account_id}/${courseID}`);
  const detail = resource?.result?.detail;
  const stale = resource?.error || ["queued", "running"].includes(resource?.result?.state);
  view.status.hidden = !!detail && !stale;
  view.list.hidden = !detail;
  setText(view.status, detail ? "章节信息更新中，保留上次结果" : resource?.error
    ? "章节信息暂时不可用，稍后重试" : resource?.loading ? "正在读取章节列表…"
      : "暂无章节缓存，等待学习进程回读");
  if (!detail) {
    return;
  }
  const isCurrentCourse = String(job.progress?.course_id) === courseID;
  const chapterKey = isCurrentCourse ? `${job.progress?.chapter_id || job.progress?.chapter || ""}/${job.status}` : "";
  if (view.detail === detail && view.chapterKey === chapterKey) return;
  view.detail = detail;
  view.chapterKey = chapterKey;
  const nodes = [];
  const keys = new Set();
  view.current = null;
  view.currentKey = "";
  for (const point of detail.points || []) {
    const key = `${job.account_id}/${courseID}/${point.id}`;
    keys.add(key);
    let entry = view.rows.get(key);
    if (!entry) {
      const row = document.createElement("div");
      const label = document.createElement("span");
      label.className = "course-detail-item-title";
      const meta = document.createElement("span");
      meta.className = "course-detail-item-meta";
      row.append(label, meta);
      entry = { row, label, meta };
      view.rows.set(key, entry);
    }
    const active = isCurrentCourse && (job.progress?.chapter_id
      ? String(point.id) === String(job.progress.chapter_id) : point.title === job.progress?.chapter);
    entry.row.className = `course-detail-item${point.completed ? " is-complete" : ""}${point.locked ? " is-locked" : ""}${active ? " is-current" : ""}`;
    entry.row.setAttribute("aria-current", active ? "step" : "false");
    setText(entry.label, point.title);
    setText(entry.meta, active ? job.status === "running" ? "正在学习" : "当前章节"
      : point.locked ? "已锁定" : point.task_count === 0 ? "无任务点" : point.completed
      ? `已完成 ${point.completed_count} / ${point.task_count}` : `剩余 ${point.remaining_count}`);
    if (active) {
      view.current = entry.row;
      view.currentKey = key;
    }
    nodes.push(entry.row);
  }
  for (const key of view.rows.keys()) if (!keys.has(key)) view.rows.delete(key);
  syncChildren(view.list, nodes);
}

function renderJobs() {
  renderJobCourseFilter();
  const ids = new Set(remoteJobs.map((job) => job.id));
  for (const id of jobViews.keys()) if (!ids.has(id)) jobViews.delete(id);
  const visibleIDs = new Set(remoteJobs.map((job) => job.id));
  for (const id of selectedJobIDs) if (!visibleIDs.has(id)) selectedJobIDs.delete(id);
  const visible = visibleJobs();
  if (!document.querySelector("#tasks-view").hidden) {
    const filtered = !!jobCourseFilter.value || !!jobSearch.value.trim() || jobFilter !== "all";
    setText(document.querySelector("#count"),
      filtered ? `${visible.length} / ${remoteJobs.length} 个任务` : `${remoteJobs.length} 个任务`);
  }
  const previousSize = jobViews.size;
  syncChildren(jobsList, visible.map((job) => renderJob(job)));
  if (previousSize !== jobViews.size && typeof consoleUIIcons === "function") consoleUIIcons();
  const empty = document.querySelector("#jobs-empty");
  empty.hidden = visible.length !== 0;
  const pacingEmpty = jobFilter === "pacing" && remoteJobs.length;
  setText(empty.querySelector("h3"),
    pacingEmpty ? "现在没有任务在拟人等待" : remoteJobs.length ? "没有匹配的任务" : "暂无课程任务");
  setText(empty.querySelector("p"),
    pacingEmpty ? "任务都在正常刷课，休息时段或随机间隔时会出现在这里"
      : remoteJobs.length ? "当前筛选结果为空" : "等待任务记录");
  renderJobSelection();
  for (const view of jobViews.values()) for (const entry of view.courses.entries.values()) {
    const outline = entry.outline;
    if (!view.item.isConnected || !entry.group.open || !outline.current || outline.scrolledKey === outline.currentKey
        || !outline.list.clientHeight) continue;
    const top = outline.current.getBoundingClientRect().top - outline.list.getBoundingClientRect().top;
    outline.list.scrollTop += top - Math.max(0, (outline.list.clientHeight - outline.current.clientHeight) / 2);
    outline.scrolledKey = outline.currentKey;
  }
}

function createArchivedAccountView(accountID) {
  const item = document.createElement("section");
  item.className = "archive-account-group";
  item.setAttribute("data-account-id", accountID);
  const heading = document.createElement("header");
  heading.className = "archive-account-heading";
  const identity = document.createElement("div");
  identity.className = "archive-account-identity";
  const title = document.createElement("h3");
  const detail = document.createElement("p");
  identity.append(title, detail);
  const count = document.createElement("span");
  count.className = "archive-account-count";
  heading.append(identity, count);
  const list = document.createElement("ul");
  list.className = "job-grid archive-account-jobs";
  list.setAttribute("aria-label", "该账号的已归档课程任务");
  item.append(heading, list);
  return { item, title, detail, count, list };
}

function archivedAccountGroups(jobs) {
  const groups = new Map();
  for (const job of fixedJobOrder(jobs)) {
    const accountID = job.account_id || "unknown";
    let group = groups.get(accountID);
    if (!group) {
      const account = remoteAccounts.find((item) => item.id === job.account_id);
      group = {
        accountID,
        title: job.label || account?.label || "未知账号",
        detail: job.username_masked || account?.username_masked || "账号信息不可用",
        jobs: [],
      };
      groups.set(accountID, group);
    }
    group.jobs.push(job);
  }
  return [...groups.values()].sort((a, b) =>
    a.title.localeCompare(b.title, "zh-CN", { numeric: true, sensitivity: "base" })
    || a.detail.localeCompare(b.detail, "zh-CN", { numeric: true, sensitivity: "base" })
    || a.accountID.localeCompare(b.accountID));
}

function renderArchivedJobs() {
  const archived = document.querySelector("#archived-view");
  archivedRefresh.disabled = !backendConnected || archivedLoading;
  const query = archivedSearch.value.trim().toLocaleLowerCase();
  const visible = fixedJobOrder(
    archivedJobs.filter((job) => job.archived_at && jobMatchesSearch(job, query)),
  );
  const groups = archivedAccountGroups(visible);
  const ids = new Set(archivedJobs.map((job) => job.id));
  for (const id of archivedJobViews.keys()) if (!ids.has(id)) archivedJobViews.delete(id);
  const accountIDs = new Set(groups.map((group) => group.accountID));
  for (const id of archivedAccountViews.keys()) {
    if (!accountIDs.has(id)) archivedAccountViews.delete(id);
  }
  const previousJobViewCount = archivedJobViews.size;
  const previousAccountViewCount = archivedAccountViews.size;
  const groupNodes = groups.map((group) => {
    let view = archivedAccountViews.get(group.accountID);
    if (!view) {
      view = createArchivedAccountView(group.accountID);
      archivedAccountViews.set(group.accountID, view);
    }
    setText(view.title, group.title);
    setText(view.detail, group.detail);
    setText(view.count, `${group.jobs.length} 个归档任务`);
    syncChildren(view.list, group.jobs.map((job) => renderJob(job, archivedJobViews, true)));
    return view.item;
  });
  syncChildren(archivedJobsList, groupNodes);
  if ((previousJobViewCount !== archivedJobViews.size || previousAccountViewCount !== archivedAccountViews.size)
      && typeof consoleUIIcons === "function") {
    consoleUIIcons();
  }
  const empty = document.querySelector("#archived-empty");
  empty.hidden = visible.length !== 0;
  setText(empty.querySelector("h3"), archivedJobs.length ? "没有匹配的归档任务" : "暂无归档任务");
  setText(empty.querySelector("p"), archivedJobs.length ? "当前搜索结果为空" : "已归档的已结束任务会显示在这里");
  setText(document.querySelector("#archived-summary"),
    `${archivedJobs.length} 条归档记录 · ${archivedAccountGroups(archivedJobs).length} 个账号`
    + " · 恢复后回到课程任务列表，不会自动重新执行");
  document.querySelector("#archived-error").hidden = !document.querySelector("#archived-error").textContent;
  if (!archived.hidden) document.querySelector("#count").textContent = `${archivedJobs.length} 个已归档`;
  for (const view of archivedJobViews.values()) {
    view.selection.disabled = true;
    view.selectionToggle.hidden = true;
    for (const button of view.actions.children) button.disabled = !canMutateJobs();
  }
}

async function refreshArchivedJobs(force = false) {
  if (!backendConnected || archivedLoading) return;
  if (!force && archivedLastRefreshAt && Date.now() - archivedLastRefreshAt < 20000) return;
  archivedLoading = true;
  archivedLastRefreshAt = Date.now();
  const generation = connectionGeneration;
  const mutation = jobMutationVersion;
  const request = ++archivedRequestGeneration;
  archivedRefresh.disabled = true;
  try {
    const result = await backendFetch("/jobs?archived=1");
    if (generation !== connectionGeneration || request !== archivedRequestGeneration
      || mutation !== jobMutationVersion) return;
    if (!Array.isArray(result.jobs)) throw new Error("归档任务响应格式不正确");
    archivedJobs = fixedJobOrder(result.jobs.filter((job) => !!job.archived_at));
    document.querySelector("#archived-error").textContent = "";
    renderArchivedJobs();
  } catch (error) {
    if (generation !== connectionGeneration || request !== archivedRequestGeneration) return;
    setText(document.querySelector("#archived-error"), error.message || "归档任务读取失败");
    document.querySelector("#archived-error").hidden = false;
  } finally {
    if (request === archivedRequestGeneration) {
      archivedLoading = false;
      archivedRefresh.disabled = !backendConnected;
    }
  }
}

function backendViewChanged(view) {
  if (view === "archived") void refreshArchivedJobs(true);
}

archivedRefresh.addEventListener("click", () => void refreshArchivedJobs(true));
archivedSearch.addEventListener("input", renderArchivedJobs);

function createOverviewJobView() {
  const item = document.createElement("li");
  item.className = "overview-job";
  const heading = document.createElement("div");
  heading.className = "overview-job-heading";
  const title = document.createElement("h3");
  const state = document.createElement("span");
  heading.append(title, state);
  const context = document.createElement("p");
  context.className = "overview-job-context";
  const activity = document.createElement("p");
  activity.className = "overview-job-activity";
  const progress = document.createElement("progress");
  progress.setAttribute("aria-label", "课程任务进度");
  const meta = document.createElement("div");
  meta.className = "overview-job-meta";
  item.append(heading, context, activity, progress, meta);
  return { item, title, state, context, activity, progress, meta };
}

function renderOverviewJob(job) {
  let view = overviewJobViews.get(job.id);
  if (!view) {
    view = createOverviewJobView();
    overviewJobViews.set(job.id, view);
  }
  const progress = job.progress || {};
  const phase = phaseLabels[progress.phase] || progress.phase || "";
  view.item.setAttribute("data-job-id", job.id);
  view.item.setAttribute("data-status", job.status);
  setText(view.title, progress.course || job.label || "课程任务");
  view.state.className = `job-state state-${job.status}`;
  setText(view.state, stateLabels[job.status] || job.status);
  setText(view.context, [progress.chapter, phase].filter(Boolean).join(" · ")
    || `课程 ID：${(job.course_ids || []).join("、")}`);

  let activity = "";
  const rollbacks = Math.max(0, Math.floor(Number(progress.rollback_count) || 0));
  const rolledBack = rollbacks > 0 && progress.phase === "video";
  view.item.classList.toggle("job-rolled-back", rolledBack);
  if (progress.phase === "video") {
    const position = Math.max(0, Math.floor(Number(progress.position) || 0));
    const duration = Math.max(0, Math.floor(Number(progress.duration) || 0));
    activity = [progress.name, duration ? `${formatSeconds(position)} / ${formatSeconds(duration)}` : "",
      videoStateLabels[progress.video_state] || "",
      rolledBack ? `平台已回退 ${rollbacks} 次` : ""].filter(Boolean).join(" · ");
  } else if (progress.phase === "workid") {
    activity = [
      progress.ai_state ? aiStateLabels[progress.ai_state] || progress.ai_state : "",
      progress.ai_type ? questionTypeLabels[progress.ai_type] || progress.ai_type : "",
      progress.quiz_state ? quizStateLabels[progress.quiz_state] || progress.quiz_state : "",
      progress.ai_error || "",
    ].filter(Boolean).join(" · ");
  } else if (progress.phase === "pacing") {
    activity = progress.name || "";
  }
  view.activity.hidden = !activity;
  setText(view.activity, activity);

  const total = Math.max(0, Math.floor(Number(progress.total) || 0));
  const completed = Math.max(0, Math.floor(Number(progress.completed) || 0));
  view.progress.hidden = total === 0;
  view.progress.max = Math.max(1, total);
  view.progress.value = Math.min(total, completed);
  const updatedAt = Number(job.updated_at || job.created_at);
  const meta = [];
  if (total) meta.push(`${completed} / ${total} 章节`);
  if (Number.isFinite(updatedAt) && updatedAt > 0) meta.push(new Date(updatedAt * 1000).toLocaleString());
  setText(view.meta, meta.join(" · "));
  view.meta.hidden = meta.length === 0;
  return view.item;
}

function renderOverviewJobs() {
  const candidates = fixedJobOrder(remoteJobs.filter(
    (job) => activeStatuses.includes(job.status) || attentionStatuses.includes(job.status),
  ));
  const visible = candidates.slice(0, 8);
  const ids = new Set(candidates.map((job) => job.id));
  for (const id of overviewJobViews.keys()) if (!ids.has(id)) overviewJobViews.delete(id);
  syncChildren(overviewJobsList, visible.map(renderOverviewJob));
  document.querySelector("#overview-jobs-empty").hidden = visible.length !== 0;
  const active = candidates.filter((job) => activeStatuses.includes(job.status)).length;
  const attention = candidates.length - active;
  const pacing = candidates.filter(pacingWaitJob).length;
  const visibleNote = candidates.length > visible.length ? ` · 显示最近 ${visible.length} 条` : "";
  setText(document.querySelector("#overview-jobs-summary"),
    `${active} 个运行或排队（${pacing} 个拟人等待） · ${attention} 个待处理${visibleNote}`);
}

function setJobFilter(key) {
  jobFilter = key;
  for (const option of jobFilterKeys) {
    document.querySelector(`#filter-${option}`).setAttribute("aria-pressed", String(key === option));
  }
  renderJobs();
}

for (const key of jobFilterKeys) {
  document.querySelector(`#filter-${key}`).addEventListener("click", () => setJobFilter(key));
}
jobSearch.addEventListener("input", renderJobs);
jobCourseFilter.addEventListener("change", renderJobs);

function setSyncState(live, message) {
  document.querySelector("#sync-status").className = `sync-label${live ? " is-live" : ""}`;
  setText(document.querySelector("#sync-message"), message);
}

function updateOverviewHealth(health = {}) {
  const backendState = document.querySelector("#overview-backend-state");
  const backendNote = document.querySelector("#overview-backend-note");
  const aiState = document.querySelector("#overview-ai-state");
  const aiNote = document.querySelector("#overview-ai-note");
  const counts = health.visible_jobs || health.jobs || {};
  const queued = (counts.queued || 0) + (counts.starting || 0) + (counts.retry_wait || 0);
  if (!health.status) {
    backendState.dataset.state = "unknown";
    setText(backendState, "未连接");
    setText(backendNote, "连接后显示刷课后端状态");
  } else if (health.status === "ok") {
    backendState.dataset.state = "online";
    setText(backendState, "在线");
    setText(backendNote, `API 可用 · 运行 ${counts.running || 0} · 排队 ${queued}`);
  } else if (health.status === "degraded" || health.status === "starting") {
    backendState.dataset.state = "degraded";
    setText(backendState, health.status === "starting" ? "启动中" : "部分异常");
    setText(backendNote, health.status === "starting" ? "后端服务正在启动" : "API 可访问，但有后台组件未就绪");
  } else {
    backendState.dataset.state = "unknown";
    setText(backendState, "未连接");
    setText(backendNote, "连接后显示刷课后端状态");
  }

  const answers = health.answers || {};
  const bank = health.question_bank || {};
  if (!health.status) {
    aiState.dataset.state = "unknown";
    setText(aiState, "待确认");
    setText(aiNote, "连接后显示 AI 工作线程状态");
  } else if (!health.ai_configured) {
    aiState.dataset.state = "unknown";
    setText(aiState, "未配置");
    setText(aiNote, `AI 未配置 · 题库 ${bank.entries || 0} 题`);
  } else if (Number.isInteger(health.ai_workers_alive) && Number.isInteger(health.ai_workers)) {
    const alive = health.ai_workers_alive;
    const total = health.ai_workers;
    aiState.dataset.state = alive === total && total > 0 ? "online" : alive > 0 ? "degraded" : "offline";
    setText(aiState, alive === total && total > 0 ? "工作线程在线" : alive > 0 ? "部分在线" : "工作线程离线");
    setText(aiNote, `${channelRouteLabel(health)} · ${alive} / ${total} 个 AI 工作线程在线 · ${answers.running || 0} 处理中 · ${answers.queued || 0} 排队`);
  } else {
    aiState.dataset.state = "unknown";
    setText(aiState, "状态未知");
    setText(aiNote, "AI 已配置，但后端未提供工作线程状态");
  }
}

function renderAIChannel(health = aiChannelHealth) {
  aiChannelHealth = health || {};
  const connected = backendConnected && Boolean(aiChannelHealth.status);
  const channel = aiChannelHealth.ai_channel === "api" ? "api" : "web";
  const apiConfigured = Boolean(aiChannelHealth.ai_api_configured);
  const visionChannel = aiChannelHealth.ai_vision_channel === "api" ? "api" : "web";
  const visionExplicit = Boolean(aiChannelHealth.ai_vision_channel_explicit);
  aiChannelWeb.setAttribute("aria-pressed", String(channel === "web"));
  aiChannelAPI.setAttribute("aria-pressed", String(channel === "api"));
  aiChannelWeb.disabled = !connected || aiChannelSaving;
  aiChannelAPI.disabled = !connected || aiChannelSaving || !apiConfigured;
  aiVisionAuto.setAttribute("aria-pressed", String(!visionExplicit));
  aiVisionWeb.setAttribute("aria-pressed", String(visionExplicit && visionChannel === "web"));
  aiVisionAPI.setAttribute("aria-pressed", String(visionExplicit && visionChannel === "api"));
  aiVisionAuto.disabled = !connected || aiChannelSaving;
  aiVisionWeb.disabled = !connected || aiChannelSaving;
  aiVisionAPI.disabled = !connected || aiChannelSaving || !apiConfigured;
  aiVisionEnabled.checked = Boolean(aiChannelHealth.ai_vision_enabled);
  aiVisionEnabled.disabled = !connected || aiChannelSaving;
  aiVisionFallback.checked = aiChannelHealth.ai_vision_fallback !== false;
  aiVisionFallback.disabled = !connected || aiChannelSaving;
  aiVisionDirect.checked = aiChannelHealth.ai_vision_direct !== false;
  aiVisionDirect.disabled = !connected || aiChannelSaving;
  if (!connected) {
    setText(aiChannelStatus, "等待后端连接");
    setText(aiChannelNote, "连接后显示并使用答题渠道");
    aiChannelError.hidden = true;
    return;
  }
  if (aiChannelSaving) {
    setText(aiChannelStatus, "正在切换答题渠道…");
    return;
  }
  const textModel = channel === "api"
    ? (aiChannelHealth.ai_api_model || "已配置模型")
    : (aiChannelHealth.ai_model || "已配置模型");
  const visionApiSeparate = Boolean(aiChannelHealth.ai_vision_api_separate);
  const visionModel = visionChannel === "api"
    ? (visionApiSeparate
      ? (aiChannelHealth.ai_vision_api_model || "已配置图片模型")
      : (aiChannelHealth.ai_api_vision_model || aiChannelHealth.ai_api_model || "已配置模型"))
    : (aiChannelHealth.ai_model || "已配置模型");
  setText(aiChannelStatus,
    `文本 ${channel === "api" ? "API AI" : "网页 AI"} · ${textModel}｜图片 ${visionChannel === "api" ? "API AI" : "网页 AI"} · ${visionModel}`);
  const visionNote = !aiChannelHealth.ai_vision_enabled
    ? "图片题自动处理已关闭：图片题不会调用 AI，会停在图片题核对等你处理。"
    : visionExplicit
      ? "图片题已单独指定通道，与文本题互不影响。"
      : "图片题当前跟随文本题通道；可以单独改成另一个通道。";
  const fallbackNote = visionChannel === "api"
    ? (aiChannelHealth.ai_vision_fallback === false
      ? "图片 API 失败就直接失败，不会改用网页 AI。"
      : "图片 API 答不出来会自动退回网页 AI 再试一次。")
    : (aiChannelHealth.ai_vision_fallback === false
      ? "网页 AI 失败就直接失败。"
      : "网页 AI 答不出来会自动改用 API 再试一次。");
  const directNote = visionChannel !== "api" ? "" : (aiChannelHealth.ai_vision_direct === false
    ? "API 图片题走识别→解题→复核三步。"
    : "API 图片题一次直答，不再走三步复核。");
  const protocol = aiChannelHealth.ai_api_protocol === "responses" ? "Responses API" : "Chat Completions";
  setText(aiChannelNote, `${visionNote} ${fallbackNote} ${directNote} API 通道使用 ${protocol}，不依赖网页 AI 登录。`);
  aiChannelError.hidden = true;
}

async function switchAIChannel(channel, kind = "text") {
  if (!backendConnected || aiChannelSaving) return;
  if (channel === "api" && !aiChannelHealth.ai_api_configured) return;
  const current = kind === "vision"
    ? (aiChannelHealth.ai_vision_channel_explicit ? aiChannelHealth.ai_vision_channel : "auto")
    : aiChannelHealth.ai_channel;
  if (channel === current) return;
  aiChannelSaving = true;
  renderAIChannel();
  try {
    const result = await backendFetch("/ai/channel", {
      method: "PUT", body: JSON.stringify(kind === "vision" ? { channel, kind } : { channel }),
    });
    aiChannelHealth = { ...aiChannelHealth, ...channelHealthFromStatus(result) };
    renderAIChannel();
  } catch (error) {
    setText(aiChannelError, error.message || "答题渠道切换失败");
    aiChannelError.hidden = false;
  } finally {
    aiChannelSaving = false;
    renderAIChannel();
  }
}

function channelRouteLabel(health) {
  const text = health?.ai_channel === "api" ? "API AI" : "网页 AI";
  if (!health?.ai_vision_enabled) return `文本 ${text} · 图片题未自动处理`;
  const vision = health?.ai_vision_channel === "api" ? "API AI" : "网页 AI";
  return `文本 ${text} · 图片 ${vision}`;
}

function channelHealthFromStatus(result) {
  return {
    ai_channel: result.channel === "api" ? "api" : "web",
    ai_vision_channel: result.vision_channel === "api" ? "api" : "web",
    ai_vision_channel_explicit: Boolean(result.vision_channel_explicit),
    ai_vision_enabled: Boolean(result.vision_enabled),
    ai_vision_fallback: result.vision_fallback !== false,
    ai_vision_direct: result.vision_direct !== false,
    ai_vision_api_separate: Boolean(result.vision_api?.separate),
    ai_vision_api_model: result.vision_api?.model || "",
    ai_api_configured: Boolean(result.api?.configured),
    ai_api_model: result.api?.model || "",
    ai_api_vision_model: result.api?.vision_model || "",
    ai_api_protocol: result.api?.protocol || "chat",
  };
}

async function switchAIVisionFallback(enabled) {
  if (!backendConnected || aiChannelSaving) return;
  if (enabled === (aiChannelHealth.ai_vision_fallback !== false)) return;
  aiChannelSaving = true;
  renderAIChannel();
  try {
    const result = await backendFetch("/ai/vision-fallback", {
      method: "PUT", body: JSON.stringify({ enabled }),
    });
    aiChannelHealth = { ...aiChannelHealth, ...channelHealthFromStatus(result) };
    renderAIChannel();
  } catch (error) {
    setText(aiChannelError, error.message || "图片题回退开关保存失败");
    aiChannelError.hidden = false;
  } finally {
    aiChannelSaving = false;
    renderAIChannel();
  }
}

async function switchAIVisionEnabled(enabled) {
  if (!backendConnected || aiChannelSaving) return;
  if (enabled === Boolean(aiChannelHealth.ai_vision_enabled)) return;
  aiChannelSaving = true;
  renderAIChannel();
  try {
    const result = await backendFetch("/ai/vision", {
      method: "PUT", body: JSON.stringify({ enabled }),
    });
    aiChannelHealth = { ...aiChannelHealth, ...channelHealthFromStatus(result) };
    renderAIChannel();
  } catch (error) {
    setText(aiChannelError, error.message || "图片题开关保存失败");
    aiChannelError.hidden = false;
  } finally {
    aiChannelSaving = false;
    renderAIChannel();
  }
}

async function switchAIVisionDirect(enabled) {
  if (!backendConnected || aiChannelSaving) return;
  if (enabled === (aiChannelHealth.ai_vision_direct !== false)) return;
  aiChannelSaving = true;
  renderAIChannel();
  try {
    const result = await backendFetch("/ai/vision-direct", {
      method: "PUT", body: JSON.stringify({ enabled }),
    });
    aiChannelHealth = { ...aiChannelHealth, ...channelHealthFromStatus(result) };
    renderAIChannel();
  } catch (error) {
    setText(aiChannelError, error.message || "图片答题方式保存失败");
    aiChannelError.hidden = false;
  } finally {
    aiChannelSaving = false;
    renderAIChannel();
  }
}

aiChannelWeb.addEventListener("click", () => void switchAIChannel("web"));
aiChannelAPI.addEventListener("click", () => void switchAIChannel("api"));
aiVisionAuto.addEventListener("click", () => void switchAIChannel("auto", "vision"));
aiVisionWeb.addEventListener("click", () => void switchAIChannel("web", "vision"));
aiVisionAPI.addEventListener("click", () => void switchAIChannel("api", "vision"));
aiVisionEnabled.addEventListener("change", () => void switchAIVisionEnabled(aiVisionEnabled.checked));
aiVisionFallback.addEventListener("change", () => void switchAIVisionFallback(aiVisionFallback.checked));
aiVisionDirect.addEventListener("change", () => void switchAIVisionDirect(aiVisionDirect.checked));

function updateDashboard(health) {
  if (typeof updateConsoleBackendStorage === "function") updateConsoleBackendStorage(health);
  const counts = health.visible_jobs || health.jobs || {};
  const answers = health.answers || {};
  setText(document.querySelector("#metric-running"), counts.running || 0);
  setText(document.querySelector("#metric-queued"), (counts.queued || 0) + (counts.starting || 0) + (counts.retry_wait || 0));
  setText(document.querySelector("#metric-ai"), (answers.running || 0) + (answers.queued || 0));
  const attention = attentionStatuses.reduce((total, status) => total + (counts[status] || 0), 0);
  setText(document.querySelector("#metric-pending"), attention);
  setText(document.querySelector("#metric-capacity"), health.max_workers ? `并发容量 ${health.max_workers}` : "等待服务状态");
  setText(document.querySelector("#metric-ai-status"), health.ai_configured
    ? `${answers.running || 0} 处理中 · ${answers.queued || 0} 排队` : "AI 未配置");
  document.querySelector("#metric-ai-status").title = "";
  if (health.question_bank) {
    const bank = health.question_bank;
    const metric = document.querySelector("#metric-ai-status");
    setText(metric, `${metric.textContent} · 题库 ${bank.entries || 0} · 命中 ${bank.hits || 0}`);
    metric.title = `共享等待 ${bank.shared_waits || 0} 次`;
  }
  const failed = (counts.failed || 0) + (counts.needs_attention || 0);
  const paused = counts.paused || 0;
  setText(document.querySelector("#metric-attention"), attention
    ? `${failed} 个异常 · ${paused} 个暂停` : "暂无待处理任务");
  updateOverviewHealth(health);
  const storage = health.storage || {};
  setText(document.querySelector("#database-status"), storage.engine === "sqlite"
    ? `后端 SQLite · WAL · ${((storage.database_bytes || 0) / 1048576).toFixed(1)} MB`
    : "等待后端存储状态");
  renderAIChannel(health);
}

function refreshDelay() {
  const active = remoteJobs.some((job) => activeStatuses.includes(job.status));
  if (document.hidden) return active ? 10000 : 20000;
  return active ? 1000 : 5000;
}

function updateOverviewUnavailable() {
  const backendState = document.querySelector("#overview-backend-state");
  backendState.dataset.state = "offline";
  setText(backendState, "连接中断");
  setText(document.querySelector("#overview-backend-note"), "无法读取刷课后端状态");
  const aiState = document.querySelector("#overview-ai-state");
  aiState.dataset.state = "unknown";
  setText(aiState, "状态未知");
  setText(document.querySelector("#overview-ai-note"), "后端连接中断，无法读取 AI 工作线程状态");
  renderAIChannel({});
}

function resetLiveCourse() {
  liveCourseGeneration += 1;
  liveCourseCatalogs.clear();
  liveCourseDetails.clear();
  for (const request of liveCourseRequests.splice(0)) request.resolve();
}

function applyLiveCourseSummary(accountID, detail) {
  if (accountID !== jobAccount.value || courseState !== "done" || !detail?.totals) return;
  const courseID = String(detail.course?.id || "");
  const course = courseItems.find((item) => item.id === courseID);
  if (!course) return;
  const summary = {
    status: "ready", ...detail.totals,
    locked_count: (detail.points || []).filter((point) => point.locked).length,
  };
  if (["status", "point_count", "task_count", "completed_count", "remaining_count", "locked_count"]
    .every((key) => course.summary?.[key] === summary[key])) return;
  courseItems = courseItems.map((item) => item.id === courseID ? { ...item, summary } : item);
  renderCourses(courseItems);
  updateCourseControls();
}

function pumpLiveCourseRequests() {
  while (liveCourseRequestCount < 4 && liveCourseRequests.length) {
    const request = liveCourseRequests.shift();
    liveCourseRequestCount += 1;
    void request.run().finally(() => {
      liveCourseRequestCount -= 1;
      request.resolve();
      pumpLiveCourseRequests();
    });
  }
}

function readLiveCourseResource(accountID, courseID = "", version = 0) {
  if (!backendConnected || !accountID) return Promise.resolve();
  const cache = courseID ? liveCourseDetails : liveCourseCatalogs;
  const key = courseID ? `${accountID}/${courseID}` : accountID;
  let entry = cache.get(key);
  if (!entry) {
    entry = { result: null, attemptedAt: null, version: 0, requestedVersion: 0, loading: false, error: false };
    cache.set(key, entry);
  }
  entry.requestedVersion = Math.max(entry.requestedVersion, Number(version) || 0);
  if (entry.loading) return entry.promise;
  if (entry.attemptedAt !== null && Date.now() - entry.attemptedAt < 20000
    && entry.requestedVersion <= entry.version) return Promise.resolve();
  const generation = liveCourseGeneration;
  const connection = connectionGeneration;
  const current = () => generation === liveCourseGeneration && connection === connectionGeneration
    && backendConnected && cache.get(key) === entry;
  entry.loading = true;
  let resolve;
  entry.promise = new Promise((done) => { resolve = done; });
  liveCourseRequests.push({ resolve, run: async () => {
    if (!current()) return;
    entry.attemptedAt = Date.now();
    entry.version = entry.requestedVersion;
    try {
      const result = await backendFetch(`/accounts/${accountID}/courses${courseID ? `/${courseID}` : ""}`);
      if (!current()) return;
      if (result.account_id !== accountID || (courseID
        ? String(result.course_id) !== courseID : !Array.isArray(result.courses))) throw new Error("Invalid course cache");
      const previous = entry.result;
      entry.result = courseID && !result.detail && previous?.detail
        ? { ...result, detail: previous.detail, updated_at: previous.updated_at } : result;
      entry.error = false;
      if (courseID && result.detail) applyLiveCourseSummary(accountID, result.detail);
    } catch (error) {
      if (current()) entry.error = true;
    } finally {
      if (current()) {
        entry.loading = false;
        renderJobs();
      }
    }
  } });
  pumpLiveCourseRequests();
  return entry.promise;
}

function refreshLiveCourse(job, courseID = job?.progress?.course_id) {
  if (!job?.account_id || !courseID) return Promise.resolve();
  courseID = String(courseID);
  const version = courseID === String(job.progress?.course_id) ? job.progress?.outline_updated_at : 0;
  return readLiveCourseResource(job.account_id, courseID, version);
}

function refreshTaskCourses() {
  if (!backendConnected) return;
  const visible = document.querySelector("#tasks-view").hidden ? [] : visibleJobs();
  const jobs = new Map(remoteJobs.filter((job) => activeStatuses.includes(job.status)).map((job) => [job.id, job]));
  for (const job of visible) jobs.set(job.id, job);
  for (const job of jobs.values()) {
    void refreshLiveCourse(job);
    void readLiveCourseResource(job.account_id);
    const view = jobViews.get(job.id)?.courses;
    if (view) for (const [courseID, entry] of view.entries) {
      if (entry.group.open) void refreshLiveCourse(job, courseID);
    }
  }
}

function normalizeManualTask(task, context = {}) {
  if (!task || typeof task !== "object" || typeof task.title !== "string") return null;
  const title = task.title.trim().slice(0, 200);
  if (!title) return null;
  const bounded = (value, length) => typeof value === "string" ? value.trim().slice(0, length) : "";
  return {
    type: bounded(task.type, 40), title,
    card_num: bounded(task.card_num, 6),
    course: bounded(task.course || context.course, 200),
    chapter: bounded(task.chapter || context.chapter, 200),
    course_id: bounded(task.course_id || context.course_id, 40),
    chapter_id: bounded(task.chapter_id || context.chapter_id, 40),
  };
}

function manualTasksFromEvents(events) {
  const chapters = new Map();
  let hasManualEvents = false;
  const chapterKey = (data) => JSON.stringify([
    String(data.course_id || data.course || ""), String(data.chapter_id || data.chapter || ""),
  ]);
  for (const event of events) {
    const data = event?.data;
    if (!data || typeof data !== "object") continue;
    const key = chapterKey(data);
    if (event.kind === "phase" && Array.isArray(data.manual_tasks)) {
      hasManualEvents = true;
      chapters.set(key, data.manual_tasks.map((task) => normalizeManualTask(task, data)).filter(Boolean));
    } else if (event.kind === "chapter_result" && data.result === "SUCCESS") {
      chapters.delete(key);
    } else if (event.kind === "course_result" && data.phase === "course_confirmed") {
      const courseID = String(data.course_id || data.course || "");
      for (const [chapter, tasks] of chapters) {
        const savedCourse = JSON.parse(chapter)[0];
        if (savedCourse === courseID || data.course_id && savedCourse === String(data.course_id)) chapters.delete(chapter);
      }
    }
  }
  const tasks = [];
  const seen = new Set();
  for (const chapterTasks of chapters.values()) for (const task of chapterTasks) {
    const key = JSON.stringify([task.course_id, task.chapter_id, task.card_num, task.type, task.title]);
    if (seen.has(key)) continue;
    seen.add(key);
    tasks.push(task);
    if (tasks.length >= 40) return { tasks, hasManualEvents };
  }
  return { tasks, hasManualEvents };
}

function manualTasksFor(job) {
  const progress = job.progress || {};
  const history = manualTaskHistories.get(job.id);
  if (attentionStatuses.includes(job.status) && history?.loaded && history.hasManualEvents) {
    return history.tasks;
  }
  const tasks = Array.isArray(progress.manual_tasks)
    ? progress.manual_tasks.map((task) => normalizeManualTask(task, progress)).filter(Boolean) : [];
  if (!tasks.length && typeof progress.name === "string" && progress.name.startsWith("需要人工处理：")) {
    for (const item of progress.name.slice("需要人工处理：".length).split("；").slice(0, 40)) {
      const match = item.match(/^(.*?)\s*\[([^\]]{1,40})\]\s*$/);
      if (match && match[1].trim()) tasks.push(normalizeManualTask({ type: match[2], title: match[1].trim() }, progress));
    }
  }
  return tasks;
}

async function loadManualTaskHistory(job) {
  const updatedAt = Number(job.updated_at) || 0;
  let entry = manualTaskHistories.get(job.id);
  if (!entry) {
    entry = { updatedAt: null, attemptedAt: 0, loading: false, loaded: false, tasks: [], hasManualEvents: false };
    manualTaskHistories.set(job.id, entry);
  }
  if (entry.loading || (entry.loaded && entry.updatedAt === updatedAt)
    || (entry.attemptedAt && Date.now() - entry.attemptedAt < 30000)) return;
  entry.loading = true;
  entry.attemptedAt = Date.now();
  const generation = connectionGeneration;
  try {
    const events = [];
    let after = 0;
    for (let page = 0; page < 3; page += 1) {
      const result = await backendFetch(`/jobs/${encodeURIComponent(job.id)}/events?after=${after}`);
      if (generation !== connectionGeneration || !backendConnected) return;
      if (!Array.isArray(result.events)) throw new Error("Invalid event history");
      let next = after;
      for (const event of result.events) {
        const id = Number(event?.id);
        if (!Number.isSafeInteger(id) || id <= next) break;
        events.push(event);
        next = id;
      }
      if (next === after) break;
      after = next;
      if (result.events.length < 200) break;
    }
    const current = remoteJobs.find((item) => item.id === job.id);
    if (!current || (Number(current.updated_at) || 0) !== updatedAt) {
      entry.attemptedAt = 0;
      return;
    }
    const history = manualTasksFromEvents(events);
    entry.tasks = history.tasks;
    entry.hasManualEvents = history.hasManualEvents;
    entry.updatedAt = updatedAt;
    entry.loaded = true;
  } catch {
    // Keep the current progress visible and retry history after a short backoff.
  } finally {
    entry.loading = false;
    if (generation === connectionGeneration && backendConnected) renderJobs();
  }
}

function refreshManualTaskHistory() {
  if (!backendConnected || document.querySelector("#tasks-view").hidden) return;
  const jobs = visibleJobs().filter((job) => attentionStatuses.includes(job.status));
  const ids = new Set(remoteJobs.map((job) => job.id));
  for (const id of manualTaskHistories.keys()) if (!ids.has(id)) manualTaskHistories.delete(id);
  for (const job of jobs) void loadManualTaskHistory(job);
}

async function refreshBackend() {
  clearTimeout(refreshTimer);
  if (!backendConnected || refreshRunning) return;
  refreshRunning = true;
  overviewRefresh.disabled = true;
  accountsRefresh.disabled = true;
  const generation = connectionGeneration;
  const accountVersion = accountMutationVersion;
  const jobVersion = jobMutationVersion;
  try {
    const [health, accounts, jobs] = await Promise.all([
      backendFetch("/health"), backendFetch("/accounts"), backendFetch("/jobs"),
    ]);
    if (generation !== connectionGeneration || accountVersion !== accountMutationVersion || jobVersion !== jobMutationVersion) return;
    backendControls.hidden = false;
    if (closeConnectionOnSuccess) {
      document.querySelector("#connection-panel").open = false;
      closeConnectionOnSuccess = false;
    }
    const counts = health.jobs || {};
    const status = health.status === "ok" ? "已连接" : "后端未就绪";
    const ai = health.ai_configured
      ? `${channelRouteLabel(health)} · AI 并发 ${health.ai_workers}`
      : "AI 未配置";
    backendStatus.textContent = `${status} · 运行 ${counts.running || 0} / ${health.max_workers} · 排队 ${counts.queued || 0} · ${ai}`;
    backendError.hidden = true;
    updateDashboard(health);
    setSyncState(health.status === "ok", health.status === "ok" ? "数据已同步" : "服务未就绪");
    setText(document.querySelector("#last-sync"), `最近同步 ${new Date().toLocaleTimeString()}`);
    remoteJobs = fixedJobOrder((jobs.jobs || []).filter((job) => !job.archived_at));
    remoteJobCount = remoteJobs.length;
    if (!document.querySelector("#tasks-view").hidden) {
      document.querySelector("#count").textContent = `${remoteJobCount} 个任务`;
    }
    accountsFresh = true;
    renderAccounts(accounts.accounts || []);
    renderJobs();
    refreshTaskCourses();
    void refreshManualTaskHistory();
    renderOverviewJobs();
    if (!document.querySelector("#archived-view").hidden) void refreshArchivedJobs();
    if (typeof insightsConnectionChanged === "function") insightsConnectionChanged(true);
  } catch (error) {
    if (generation !== connectionGeneration || accountVersion !== accountMutationVersion || jobVersion !== jobMutationVersion) return;
    backendStatus.textContent = "连接中断 · 状态未更新";
    accountsFresh = false;
    renderAccountCards();
    renderJobSelection();
    renderArchivedJobs();
    updateCourseControls();
    setSyncState(false, "等待重新连接");
    updateOverviewUnavailable();
    if (typeof insightsConnectionChanged === "function") insightsConnectionChanged(false, true);
    backendError.textContent = error.message || "连接失败";
    backendError.hidden = false;
    closeConnectionOnSuccess = false;
    document.querySelector("#connection-panel").open = true;
  } finally {
    refreshRunning = false;
    overviewRefresh.disabled = !backendConnected;
    accountsRefresh.disabled = !backendConnected;
    if (backendConnected) {
      refreshTimer = setTimeout(refreshBackend, jobVersion !== jobMutationVersion ? 0 : refreshDelay());
    }
  }
}

document.addEventListener("visibilitychange", () => {
  if (!document.hidden && backendConnected) refreshBackend();
});

(async () => {
  try {
    const config = await backendFetch("");
    backendURL.value = config.url || "http://127.0.0.1:3100";
    backendConnected = config.configured;
    if (backendConnected) await refreshBackend();
  } catch (error) {
    backendError.textContent = error.message || "配置读取失败";
    backendError.hidden = false;
  }
})();
