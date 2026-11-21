const form = document.querySelector("#form");
const titleInput = document.querySelector("#title");
const detailInput = document.querySelector("#detail");
const statusInput = document.querySelector("#status");
const saveButton = document.querySelector("#save");
const cancelButton = document.querySelector("#cancel");
const formError = document.querySelector("#form-error");
const list = document.querySelector("#list");
const empty = document.querySelector("#empty");
const count = document.querySelector("#count");
const loadError = document.querySelector("#load-error");

const labels = { todo: "待办", doing: "进行中", done: "完成" };
let editingID = "";
let noteCount = 0;

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  formError.hidden = true;
  const payload = {
    title: titleInput.value,
    detail: detailInput.value,
    status: statusInput.value,
  };
  const path = editingID ? `/api/items/${editingID}` : "/api/items";
  const method = editingID ? "PUT" : "POST";
  saveButton.disabled = true;
  try {
    const response = await fetch(path, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(10000),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.error || "保存失败");
    }
    resetForm();
    await loadItems();
  } catch (error) {
    formError.textContent = error.message || "保存失败，请重试";
    formError.hidden = false;
  } finally {
    saveButton.disabled = false;
  }
});

cancelButton.addEventListener("click", resetForm);

async function loadItems() {
  loadError.hidden = true;
  try {
    const response = await fetch("/api/items", { signal: AbortSignal.timeout(10000) });
    if (!response.ok) throw new Error("读取失败");
    const body = await response.json();
    const items = body.items || [];
    noteCount = items.length;
    if (!document.querySelector("#notes-view").hidden) count.textContent = `${items.length} 条`;
    empty.hidden = items.length !== 0;
    list.replaceChildren(...items.map(renderItem));
    consoleUIIcons();
  } catch {
    loadError.textContent = "工作事项暂不可用，保留上次数据";
    loadError.hidden = false;
  }
}

function renderItem(item) {
  const li = document.createElement("li");
  const title = document.createElement("h2");
  title.textContent = item.title;
  const detail = document.createElement("p");
  detail.textContent = item.detail || "";
  const meta = document.createElement("div");
  meta.className = "meta";
  const status = document.createElement("span");
  status.textContent = labels[item.status] || item.status;
  const time = document.createElement("time");
  time.dateTime = item.updatedAt;
  time.textContent = formatTime(item.updatedAt);
  meta.append(status, time);

  const actions = document.createElement("div");
  actions.className = "actions";
  const edit = document.createElement("button");
  edit.type = "button";
  edit.className = "ghost icon-button";
  edit.title = "修改";
  edit.setAttribute("aria-label", "修改");
  const editIcon = document.createElement("i");
  editIcon.setAttribute("data-lucide", "pencil");
  edit.append(editIcon);
  edit.addEventListener("click", () => startEdit(item));
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "ghost icon-button danger";
  remove.title = "删除";
  remove.setAttribute("aria-label", "删除");
  const removeIcon = document.createElement("i");
  removeIcon.setAttribute("data-lucide", "trash-2");
  remove.append(removeIcon);
  remove.addEventListener("click", () => removeItem(item.id));
  actions.append(edit, remove);
  li.append(title, detail, meta, actions);
  return li;
}

function startEdit(item) {
  editingID = item.id;
  titleInput.value = item.title;
  detailInput.value = item.detail || "";
  statusInput.value = item.status;
  consoleActionLabel(saveButton, "save", "保存");
  cancelButton.hidden = false;
  titleInput.focus();
}

function resetForm() {
  editingID = "";
  form.reset();
  consoleActionLabel(saveButton, "plus", "添加");
  cancelButton.hidden = true;
  formError.hidden = true;
}

async function removeItem(id) {
  try {
    const response = await fetch(`/api/items/${id}`, { method: "DELETE", signal: AbortSignal.timeout(10000) });
    if (!response.ok) throw new Error("删除失败");
    if (editingID === id) resetForm();
    await loadItems();
  } catch {
    loadError.textContent = "删除失败，请重试";
    loadError.hidden = false;
  }
}

function formatTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString();
}

loadItems();
