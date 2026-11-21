let currentUser = null;
let sessionRefreshPromise = null;

function sessionQuotaText(user) {
  if (user.role === "owner") return "最高管理员";
  if (user.plan_active && user.plan_expires_at) {
    const expires = new Date(Number(user.plan_expires_at) * 1000);
    return `月卡至 ${expires.toLocaleDateString("zh-CN")} · 单次 ≤ ${user.plan_course_limit || 0} 门`;
  }
  return `剩余 ${Number(user.credits) || 0} 门课程`;
}

function applySessionUser(user) {
  currentUser = user;
  const owner = user.role === "owner";
  document.body.classList.toggle("role-owner", owner);
  document.body.classList.toggle("role-user", !owner);
  for (const element of document.querySelectorAll("[data-owner-only]")) {
    element.hidden = !owner;
  }
  const name = document.querySelector("#session-user-name");
  name.textContent = `${user.username} · ${sessionQuotaText(user)}`;
  name.title = owner ? "最高管理员" : sessionQuotaText(user);
  document.querySelector("#sidebar-user-name").textContent = name.textContent;
}

async function refreshSession() {
  if (sessionRefreshPromise) return sessionRefreshPromise;
  sessionRefreshPromise = (async () => {
    const response = await fetch("/api/session", { cache: "no-store" });
    if (response.status === 401) {
      location.replace("/login.html");
      throw new Error("请先登录");
    }
    if (!response.ok) throw new Error("无法读取登录状态");
    const result = await response.json();
    applySessionUser(result.user);
    return result.user;
  })().finally(() => { sessionRefreshPromise = null; });
  return sessionRefreshPromise;
}

const currentUserPromise = refreshSession();

document.querySelector("#logout").addEventListener("click", async () => {
  try {
    await fetch("/api/session", { method: "DELETE" });
  } finally {
    location.replace("/login.html");
  }
});
// 手机上顶栏空间紧张，修改密码与退出放在侧边抽屉里，复用同一套处理逻辑。
// 打开密码对话框前先收起抽屉：抽屉展开时 main 是 inert 的，对话框在 main 里。
document.querySelector("#sidebar-change-password").addEventListener("click", () => {
  if (typeof setConsoleSidebar === "function") setConsoleSidebar(false);
  document.querySelector("#change-password").click();
});
document.querySelector("#sidebar-logout").addEventListener("click", () => {
  document.querySelector("#logout").click();
});

const passwordDialog = document.querySelector("#password-dialog");
document.querySelector("#change-password").addEventListener("click", () => {
  document.querySelector("#password-form").reset();
  document.querySelector("#password-error").hidden = true;
  passwordDialog.showModal();
  document.querySelector("#password-current").focus();
});
for (const id of ["password-dialog-close", "password-cancel"]) {
  document.querySelector(`#${id}`).addEventListener("click", () => passwordDialog.close());
}
document.querySelector("#password-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const error = document.querySelector("#password-error");
  error.hidden = true;
  try {
    const response = await fetch("/api/session/password", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        current_password: document.querySelector("#password-current").value,
        new_password: document.querySelector("#password-new").value,
      }),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || "密码修改失败");
    passwordDialog.close();
  } catch (failure) {
    error.textContent = failure.message || "密码修改失败";
    error.hidden = false;
  }
});
