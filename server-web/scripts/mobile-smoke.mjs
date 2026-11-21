/* 手机端交互冒烟：底部导航、侧边抽屉、底部抽屉式弹窗、吸顶筛选条。
 *
 * layout metrics live in mobile-audit.mjs; this file proves the phone-only *behaviour*
 * that metrics cannot see, at 390x844 (iPhone 14) against the stub backend:
 *
 *   1. 底部导航只在手机上出现，四个视图一键切换，aria-pressed 跟随当前视图
 *   2. 更多 → 侧边抽屉打开：mask 出现、main 变 inert、Esc 关闭并把焦点还给触发按钮
 *   3. 顶栏在手机上不含修改密码/退出，抽屉里有，且点击后能打开密码对话框
 *   4. 弹窗在手机上贴底成抽屉：宽度等于视口、底边贴住视口、页脚可见
 *   5. 任务筛选条滚动时吸顶，五个分类都能点
 *   6. 底部导航不高过内容：页面底部留白 >= 导航高度
 *
 *   node scripts/mobile-smoke.mjs [screenshot-dir]
 */
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";
import { launchBrowser, startConsoleStub } from "./mobile-fixtures.mjs";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright-core");
const output = path.resolve(process.argv[2] || "mobile-smoke");
await fs.mkdir(output, { recursive: true });

const WIDTH = 390;
const HEIGHT = 844;
const stub = await startConsoleStub();
const browser = await launchBrowser(chromium);
const results = [];
try {
  const context = await browser.newContext({
    viewport: { width: WIDTH, height: HEIGHT }, isMobile: true, hasTouch: true, deviceScaleFactor: 2,
  });
  await context.route("**/*", (route) => new URL(route.request().url()).origin === stub.origin
    ? route.continue() : route.abort());
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(stub.origin, { waitUntil: "networkidle" });
  await page.waitForFunction(() => typeof backendConnected !== "undefined" && backendConnected);

  const box = async (selector) => page.locator(selector).boundingBox();
  const pressed = (selector) => page.locator(selector).getAttribute("aria-pressed");
  const visibleView = () => page.evaluate(
    () => [...document.querySelectorAll("main > section[role='tabpanel']")].find((node) => !node.hidden)?.id);

  // 1. 底部导航：只在手机上出现，四个视图一键切换。
  assert.equal(await page.locator("#mobile-nav").isVisible(), true);
  for (const [label, view] of [["任务", "tasks"], ["账号", "accounts"], ["节奏", "pacing"], ["总览", "overview"]]) {
    await page.locator(`#mobile-nav button[data-view="${view}"]`).click();
    await page.waitForTimeout(160);
    assert.equal(await visibleView(), `${view}-view`, `${label} 应切到 ${view}`);
    assert.equal(await pressed(`#mobile-nav button[data-view="${view}"]`), "true");
    const others = await page.evaluate((current) => [...document.querySelectorAll("#mobile-nav button[data-view]")]
      .filter((node) => node.dataset.view !== current)
      .every((node) => node.getAttribute("aria-pressed") === "false"), view);
    assert.ok(others, `${view} 之外的底部导航项应未选中`);
  }

  // 2. 更多 → 抽屉：mask 出现、main inert、Esc 关闭并归还焦点。
  await page.locator("#mobile-more").click();
  await page.waitForTimeout(160);
  assert.equal(await page.evaluate(() => document.body.classList.contains("nav-open")), true);
  assert.equal(await page.locator("#sidebar-mask").isVisible(), true);
  assert.equal(await page.evaluate(() => document.querySelector("main").inert), true);
  const sidebarBox = await box("#sidebar");
  assert.ok(sidebarBox.x >= -1, "抽屉应滑入视口");
  await page.keyboard.press("Escape");
  await page.waitForTimeout(160);
  assert.equal(await page.evaluate(() => document.body.classList.contains("nav-open")), false);
  assert.equal(await page.evaluate(() => document.activeElement?.id), "mobile-more", "焦点应回到更多按钮");

  // 3. 顶栏删繁就简：修改密码/退出只保留在抽屉里。
  assert.equal(await page.locator("#change-password").isVisible(), false);
  assert.equal(await page.locator("#logout").isVisible(), false);
  await page.locator("#mobile-more").click();
  await page.waitForTimeout(160);
  assert.equal(await page.locator("#sidebar-logout").isVisible(), true);
  assert.match(await page.locator("#sidebar-user-name").textContent(), /owner/);
  await page.locator("#sidebar-change-password").click();
  await page.waitForTimeout(200);
  assert.equal(await page.locator("#password-dialog").evaluate((node) => node.open), true);
  assert.equal(await page.evaluate(() => document.body.classList.contains("nav-open")), false,
    "打开密码对话框时应先收起抽屉");
  await page.locator("#password-cancel").click();
  await page.waitForTimeout(160);

  // 4. 弹窗贴底成抽屉。
  await page.locator("#mobile-nav button[data-view='tasks']").click();
  await page.waitForTimeout(200);
  await page.locator("#add-job").click();
  await page.waitForTimeout(250);
  const dialog = await page.locator("#job-dialog").boundingBox();
  assert.equal(Math.round(dialog.width), WIDTH, "弹窗宽度应等于视口宽度");
  assert.ok(Math.abs(dialog.y + dialog.height - HEIGHT) <= 2, `弹窗底边应贴住视口 (${dialog.y + dialog.height})`);
  assert.ok(dialog.height <= HEIGHT * 0.94, "弹窗高度不应超过 92dvh");
  const footer = await box("#job-dialog .job-dialog-footer");
  assert.ok(footer && footer.y + footer.height <= HEIGHT + 1, "页脚应在视口内");
  await page.screenshot({ path: path.join(output, "dialog-sheet.png") });
  await page.locator("#job-editor-cancel").click();
  await page.waitForTimeout(200);

  // 5. 筛选条吸顶 + 五个分类可点。
  const topbar = await box(".topbar");
  const before = await box("#tasks-view .jobs-toolbar");
  await page.evaluate(() => window.scrollTo(0, 900));
  await page.waitForTimeout(200);
  const after = await box("#tasks-view .jobs-toolbar");
  assert.ok(before && after, "筛选条应可见");
  assert.ok(after.y < before.y - 100, `滚动后筛选条应吸顶而不是滚走 (${after.y} vs ${before.y})`);
  assert.ok(Math.abs(after.y - topbar.height) <= 2,
    `筛选条应贴在顶栏下方 (${after.y} vs ${topbar.height})`);
  await page.evaluate(() => window.scrollTo(0, 0));
  for (const key of ["all", "active", "pacing", "attention", "finished"]) {
    await page.locator(`#filter-${key}`).click();
    await page.waitForTimeout(120);
    assert.equal(await pressed(`#filter-${key}`), "true");
  }
  await page.locator("#filter-pacing").click();
  await page.waitForTimeout(120);

  // 6. 内容不被底部导航遮住。
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await page.waitForTimeout(200);
  const pageFooter = await box(".workspace-footer");
  const nav = await box("#mobile-nav");
  assert.ok(nav.y > HEIGHT - 100, "底部导航应贴在屏幕底部");
  assert.ok(pageFooter.y + pageFooter.height <= nav.y + 1,
    `页脚不应被底部导航遮挡 (${pageFooter.y + pageFooter.height} > ${nav.y})`);
  assert.deepEqual(errors, []);

  await page.evaluate(() => window.scrollTo(0, 0));
  for (const view of ["overview", "tasks", "pacing"]) {
    await page.locator(`#mobile-nav button[data-view='${view}']`).click();
    await page.waitForTimeout(220);
    await page.screenshot({ path: path.join(output, `${view}-390.png`), fullPage: false });
    results.push(view);
  }
  // 平板：底部导航消失，抽屉仍需菜单按钮。
  await page.setViewportSize({ width: 768, height: 1024 });
  await page.waitForTimeout(220);
  assert.equal(await page.locator("#mobile-nav").isVisible(), false, "平板不显示底部导航");
  assert.equal(await page.locator("#toggle-sidebar").isVisible(), true, "平板保留侧边菜单按钮");
  assert.equal(await page.locator("#change-password").isVisible(), true, "平板顶栏恢复修改密码");

  console.log(JSON.stringify({ ok: true, screenshots: results, output }));
  await context.close();
} finally {
  await browser.close();
  await stub.close();
}
