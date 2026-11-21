const usersList = document.querySelector("#users-list");
const usersError = document.querySelector("#users-error");
const usersEmpty = document.querySelector("#users-empty");
const usersSummary = document.querySelector("#users-summary");
const userDialog = document.querySelector("#user-dialog");
const userForm = document.querySelector("#user-form");
const userFormError = document.querySelector("#user-form-error");
const userAccountOptions = document.querySelector("#user-account-options");
let users = [];
let userAccounts = [];
let editingUserID = "";
let usersLoading = false;
let userSaving = false;

async function usersFetch(path = "", options = {}) {
  const response = await fetch("/api/users" + path, {
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
    signal: AbortSignal.timeout(18000),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(errorLabels[body.error] || body.error || `请求失败 (${response.status})`);
  return body;
}

function usersSetText(node, value) { node.textContent = value || ""; }

function userPlanText(user) {
  if (user.role === "owner") return "最高管理员";
  if (user.plan_active) {
    const date = new Date(Number(user.plan_expires_at) * 1000).toLocaleDateString("zh-CN");
    return `月卡至 ${date} · 单次 ≤ ${user.plan_course_limit} 门`;
  }
  return `剩余 ${user.credits || 0} 门课程`;
}

function createUserCard(user) {
  const item = document.createElement("li");
  item.className = "user-card";
  item.setAttribute("data-user-id", user.id);
  const identity = document.createElement("div");
  identity.className = "user-identity";
  const name = document.createElement("strong");
  const meta = document.createElement("span");
  identity.append(name, meta);
  const state = document.createElement("span");
  state.className = "user-status";
  const quota = document.createElement("p");
  quota.className = "user-quota";
  const accounts = document.createElement("p");
  accounts.className = "user-accounts";
  const actions = document.createElement("div");
  actions.className = "actions user-actions";
  if (user.role !== "owner") {
    const edit = document.createElement("button");
    edit.type = "button";
    edit.className = "ghost compact";
    edit.textContent = "编辑";
    edit.addEventListener("click", () => openUserDialog(user));
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "ghost compact danger";
    remove.textContent = "删除";
    remove.addEventListener("click", () => deleteUser(user));
    actions.append(edit, remove);
  }
  item.append(identity, state, quota, accounts, actions);
  return { item, name, meta, state, quota, accounts, actions };
}

function renderUsers() {
  usersList.replaceChildren(...users.map((user) => {
    const view = createUserCard(user);
    usersSetText(view.name, user.username);
    usersSetText(view.meta, user.role === "owner" ? "最高管理员" : `创建于 ${new Date(Number(user.created_at) * 1000).toLocaleDateString("zh-CN")}`);
    usersSetText(view.state, user.status === "active" ? "正常" : "已停用");
    view.state.dataset.state = user.status;
    usersSetText(view.quota, userPlanText(user));
    usersSetText(view.accounts, (user.account_ids || []).length
      ? `已分配 ${user.account_ids.length} 个学习账号` : "未分配学习账号");
    return view.item;
  }));
  usersEmpty.hidden = users.length > 0;
  usersEmpty.querySelector("h3").textContent = usersLoading ? "正在读取用户" : "暂无普通用户";
  usersEmpty.querySelector("p").textContent = usersLoading ? "请稍候" : "点击“新增用户”创建账号";
  const regular = users.filter((user) => user.role !== "owner");
  usersSummary.textContent = `${regular.length} 个普通用户 · ${regular.filter((user) => user.status === "active").length} 个正常`;
  usersError.hidden = !usersError.textContent;
}

async function loadUsers(force = false) {
  if (!currentUser || currentUser.role !== "owner" || usersLoading) return;
  usersLoading = true;
  usersError.textContent = "";
  renderUsers();
  try {
    const [result, accountResult] = await Promise.all([usersFetch(), backendFetch("/accounts")]);
    users = result.users || [];
    userAccounts = accountResult.accounts || [];
  } catch (error) {
    usersError.textContent = error.message || "用户列表读取失败";
  } finally {
    usersLoading = false;
    renderUsers();
  }
}

function usersViewChanged(view) {
  if (view === "users") void loadUsers();
}

document.querySelector("#users-refresh").addEventListener("click", () => void loadUsers(true));
document.querySelector("#add-user").addEventListener("click", () => openUserDialog(null));

function selectedUserAccountIDs() {
  return [...userAccountOptions.querySelectorAll('input[type="checkbox"]:checked')]
    .map((input) => input.value);
}

function renderUserAccountOptions(user) {
  const selected = new Set(user?.account_ids || []);
  const nodes = userAccounts.map((account) => {
    const label = document.createElement("label");
    label.className = "user-account-option";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = account.id;
    input.checked = selected.has(account.id);
    const text = document.createElement("span");
    text.textContent = `${account.label || "未命名账号"} (${account.username_masked || "已加密"})`;
    label.append(input, text);
    input.addEventListener("change", () => {
      document.querySelector("#user-account-count").textContent = `${selectedUserAccountIDs().length} 个已选`;
    });
    return label;
  });
  if (!nodes.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "后端没有可分配的学习账号";
    nodes.push(empty);
  }
  userAccountOptions.replaceChildren(...nodes);
  document.querySelector("#user-account-count").textContent = `${selected.size} 个已选`;
}

function localDateValue(seconds) {
  if (!seconds) return "";
  const date = new Date(Number(seconds) * 1000);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function openUserDialog(user) {
  if (!currentUser || currentUser.role !== "owner") return;
  editingUserID = user?.id || "";
  document.querySelector("#user-dialog-title").textContent = user ? `编辑用户 · ${user.username}` : "新增用户";
  document.querySelector("#user-username").value = user?.username || "";
  document.querySelector("#user-username").disabled = Boolean(user);
  document.querySelector("#user-password").value = "";
  document.querySelector("#user-password").placeholder = user ? "留空表示不修改" : "至少 8 位";
  document.querySelector("#user-password").required = !user;
  document.querySelector("#user-status").value = user?.status || "active";
  document.querySelector("#user-credits").value = user?.credits ?? 0;
  document.querySelector("#user-plan-enabled").checked = Boolean(user?.plan_active);
  document.querySelector("#user-plan-date").value = user?.plan_active ? localDateValue(user.plan_expires_at) : "";
  document.querySelector("#user-plan-limit").value = user?.plan_course_limit || 1;
  userFormError.hidden = true;
  renderUserAccountOptions(user);
  updateUserPlanControls();
  userDialog.showModal();
  if (!user) document.querySelector("#user-username").focus();
}

function updateUserPlanControls() {
  const enabled = document.querySelector("#user-plan-enabled").checked;
  document.querySelector("#user-plan-date").disabled = !enabled;
  document.querySelector("#user-plan-limit").disabled = !enabled;
}

document.querySelector("#user-plan-enabled").addEventListener("change", updateUserPlanControls);
for (const id of ["user-dialog-close", "user-dialog-cancel"]) {
  document.querySelector(`#${id}`).addEventListener("click", () => {
    if (!userSaving) userDialog.close();
  });
}

userForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (userSaving) return;
  userSaving = true;
  userFormError.hidden = true;
  const planEnabled = document.querySelector("#user-plan-enabled").checked;
  const dateValue = document.querySelector("#user-plan-date").value;
  let planExpires = null;
  if (planEnabled) {
    if (!dateValue) {
      userFormError.textContent = "请选择套餐到期日";
      userFormError.hidden = false;
      userSaving = false;
      return;
    }
    const date = new Date(`${dateValue}T23:59:59`);
    planExpires = date.getTime() / 1000;
    if (!Number.isFinite(planExpires) || planExpires <= Date.now() / 1000) {
      userFormError.textContent = "套餐到期日必须晚于今天";
      userFormError.hidden = false;
      userSaving = false;
      return;
    }
  }
  const body = {
    username: document.querySelector("#user-username").value.trim(),
    password: document.querySelector("#user-password").value,
    status: document.querySelector("#user-status").value,
    credits: Number(document.querySelector("#user-credits").value) || 0,
    plan_expires_at: planEnabled ? planExpires : null,
    plan_course_limit: planEnabled ? Number(document.querySelector("#user-plan-limit").value) || 1 : null,
  };
  try {
    const result = await usersFetch(editingUserID ? `/${editingUserID}` : "", {
      method: editingUserID ? "PUT" : "POST",
      body: JSON.stringify(body),
    });
    const user = result.user;
    await usersFetch(`/${user.id}/accounts`, {
      method: "PUT", body: JSON.stringify({ account_ids: selectedUserAccountIDs() }),
    });
    userDialog.close();
    await loadUsers(true);
  } catch (error) {
    userFormError.textContent = error.message || "保存用户失败";
    userFormError.hidden = false;
  } finally {
    userSaving = false;
  }
});

async function deleteUser(user) {
  if (!confirm(`删除用户 ${user.username}？该用户的账号分配将被清除。`)) return;
  try {
    await usersFetch(`/${user.id}`, { method: "DELETE" });
    await loadUsers(true);
  } catch (error) {
    usersError.textContent = error.message || "删除用户失败";
    renderUsers();
  }
}
