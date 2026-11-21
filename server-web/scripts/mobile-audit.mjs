/* 手机端布局审计（metric-first）。
 *
 * Renders every console view at phone / tablet / desktop widths against the stub backend and
 * reports the things that actually break a small screen, per view and per width:
 *
 *   overflow        document horizontal scroll (px)
 *   outside         visible elements spilling past the viewport edges
 *   small-tap-target interactive controls under 40px (18px is enough for a checkbox)
 *   tiny-text       text under 11px — unreadable on a phone
 *
 * Exit code 1 when a phone/tablet width regresses, so it doubles as the mobile smoke gate:
 *   AUDIT_WIDTHS=360,390,414,768 node scripts/mobile-audit.mjs out
 * Writes report.json / offenders.json plus one full-page screenshot per width.
 */
import fs from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";
import { launchBrowser, startConsoleStub, VIEWS } from "./mobile-fixtures.mjs";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright-core");
const output = path.resolve(process.argv[2] || "mobile-audit");
await fs.mkdir(output, { recursive: true });

const WIDTHS = (process.env.AUDIT_WIDTHS || "360,390,414,768,900,1024,1440")
  .split(",").map((value) => Number(value.trim())).filter(Boolean);
const HEIGHT = Number(process.env.AUDIT_HEIGHT) || 900;

const MEASURE = () => {
  const problems = [];
  const seen = new Set();
  const sidebar = document.querySelector(".sidebar");
  const sidebarOffscreen = sidebar && sidebar.getBoundingClientRect().right <= 1;
  const offCanvas = (node) => sidebarOffscreen && sidebar.contains(node);
  const describe = (node) => {
    const id = node.id ? `#${node.id}` : "";
    const cls = String(node.className || "").split(/\s+/).filter(Boolean).slice(0, 3).join(".");
    const text = (node.textContent || "").trim().replace(/\s+/g, " ").slice(0, 40);
    return `${node.tagName.toLowerCase()}${id}${cls ? "." + cls : ""}${text ? ` "${text}"` : ""}`;
  };
  const visible = (node) => node.getClientRects().length > 0
    && getComputedStyle(node).visibility !== "hidden"
    && getComputedStyle(node).opacity !== "0";

  const overflow = document.documentElement.scrollWidth - innerWidth;
  for (const node of document.querySelectorAll("#topbar *, main *, .sidebar *")) {
    if (!(node instanceof HTMLElement) || !visible(node) || offCanvas(node)) continue;
    const rect = node.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) continue;
    if (rect.right > innerWidth + 1 || rect.left < -1) {
      const key = describe(node);
      if (seen.has(key)) continue;
      seen.add(key);
      problems.push({ type: "outside-viewport", node: key,
        left: Math.round(rect.left), right: Math.round(rect.right) });
    }
  }
  for (const node of document.querySelectorAll("main button, main input, main select, main textarea, main summary, .sidebar button")) {
    if (!(node instanceof HTMLElement) || !visible(node) || node.disabled || offCanvas(node)) continue;
    const rect = node.getBoundingClientRect();
    const iconOnly = !(node.textContent || "").trim() && !node.getAttribute("aria-label")?.length;
    const checkbox = node.type === "checkbox" || node.type === "radio";
    const minHeight = checkbox ? 18 : 40;
    const minWidth = checkbox ? 18 : iconOnly ? 0 : 32;
    if (rect.height + 0.5 < minHeight || rect.width + 0.5 < minWidth) {
      problems.push({ type: "small-tap-target", node: describe(node),
        height: Math.round(rect.height), width: Math.round(rect.width) });
    }
  }
  for (const node of document.querySelectorAll("main p, main span, main div, main td, main li")) {
    if (!(node instanceof HTMLElement) || !visible(node) || node.children.length || offCanvas(node)) continue;
    const text = (node.textContent || "").trim();
    if (!text) continue;
    const size = parseFloat(getComputedStyle(node).fontSize);
    if (size < 11) problems.push({ type: "tiny-text", node: describe(node), fontSize: size });
  }

  // 文字与背景的对比度：手机在户外看，浅灰小字最容易糊成一片。
  const luminance = (color) => {
    const [r, g, b] = color.map((value) => {
      const channel = value / 255;
      return channel <= 0.03928 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const parseColor = (value) => {
    const match = String(value).match(/rgba?\(([^)]+)\)/);
    if (!match) return null;
    const parts = match[1].split(",").map((item) => Number(item.trim()));
    if (parts.length < 3 || parts.some((item) => Number.isNaN(item))) return null;
    return { rgb: parts.slice(0, 3), alpha: parts.length > 3 ? parts[3] : 1 };
  };
  const backgroundOf = (node) => {
    let current = node;
    while (current && current !== document.documentElement) {
      const color = parseColor(getComputedStyle(current).backgroundColor);
      if (color && color.alpha > 0.85) return color.rgb;
      current = current.parentElement;
    }
    return [255, 255, 255];
  };
  for (const node of document.querySelectorAll("main p, main span, main li, main td, main strong")) {
    if (!(node instanceof HTMLElement) || !visible(node) || node.children.length || offCanvas(node)) continue;
    const text = (node.textContent || "").trim();
    if (text.length < 2) continue;
    const style = getComputedStyle(node);
    const color = parseColor(style.color);
    if (!color) continue;
    const size = parseFloat(style.fontSize);
    const weight = Number(style.fontWeight) || 400;
    const background = backgroundOf(node);
    const foreground = color.rgb.map((value, index) => value * color.alpha + background[index] * (1 - color.alpha));
    const lightest = Math.max(luminance(foreground), luminance(background));
    const darkest = Math.min(luminance(foreground), luminance(background));
    const ratio = (lightest + 0.05) / (darkest + 0.05);
    const large = size >= 18.66 || (size >= 14 && weight >= 600);
    const required = large ? 3 : 4.5;
    if (ratio + 0.05 < required) {
      problems.push({ type: "low-contrast", node: describe(node), ratio: Math.round(ratio * 100) / 100,
        required, fontSize: size, color: style.color, background: `rgb(${background.join(", ")})` });
    }
  }
  return { overflow, problems };
};

const stub = await startConsoleStub();
const browser = await launchBrowser(chromium);
const report = { widths: WIDTHS, views: {}, totals: {} };
const counts = { overflow: 0, outside: 0, taps: 0, tiny: 0, contrast: 0, errors: 0 };
try {
  for (const width of WIDTHS) {
    const context = await browser.newContext({
      viewport: { width, height: HEIGHT }, deviceScaleFactor: 1, isMobile: width <= 520, hasTouch: width <= 900,
    });
    await context.route("**/*", (route) => new URL(route.request().url()).origin === stub.origin
      ? route.continue() : route.abort());
    const page = await context.newPage();
    if (process.env.AUDIT_DEBUG) {
      page.on("response", (response) => console.log("RESP", response.status(),
        response.headers()["content-type"], response.url().replace(stub.origin, "")));
      page.on("requestfailed", (request) => console.log("FAILED", request.url().replace(stub.origin, "")));
    }
    await page.addInitScript(() => {
      window.__auditErrors = [];
      addEventListener("error", (event) => window.__auditErrors.push(
        `${event.message} @ ${String(event.filename).split("/").pop()}:${event.lineno}:${event.colno}`));
      addEventListener("unhandledrejection", (event) => window.__auditErrors.push(
        `rejection ${String(event.reason && event.reason.message || event.reason)}`));
    });
    const errors = [];
    await page.goto(stub.origin, { waitUntil: "networkidle" });
    await page.waitForFunction(() => typeof backendConnected !== "undefined" && backendConnected);
    for (const view of VIEWS) {
      await page.evaluate((name) => window.consoleSelectView(name), view);
      await page.waitForTimeout(220);
      const viewErrors = await page.evaluate(() => window.__auditErrors.splice(0));
      if (viewErrors.length) errors.push(...viewErrors.map((message) => `${view}: ${message}`));
      const result = await page.evaluate(MEASURE);
      report.views[`${width}:${view}`] = result;
      if (result.overflow > 0) counts.overflow += 1;
      for (const problem of result.problems) {
        if (problem.type === "outside-viewport") counts.outside += 1;
        if (problem.type === "small-tap-target") counts.taps += 1;
        if (problem.type === "tiny-text") counts.tiny += 1;
        if (problem.type === "low-contrast") counts.contrast += 1;
      }
    }
    counts.errors += errors.length;
    report[`errors-${width}`] = errors;
    await page.evaluate(() => window.consoleSelectView("tasks"));
    await page.waitForTimeout(150);
    await page.screenshot({ path: path.join(output, `mobile-${width}.png`), fullPage: true });
    await context.close();
  }
  report.totals = counts;
  await fs.writeFile(path.join(output, "report.json"), JSON.stringify(report, null, 2));

  // Per-width roll-up: the only numbers worth watching when tuning a breakpoint.
  const byWidth = {};
  for (const [key, value] of Object.entries(report.views)) {
    const width = Number(key.split(":")[0]);
    const bucket = byWidth[width] || (byWidth[width] = { tiny: 0, taps: 0, outside: 0, overflow: 0, contrast: 0 });
    bucket.overflow += value.overflow > 0 ? 1 : 0;
    for (const problem of value.problems) {
      if (problem.type === "tiny-text") bucket.tiny += 1;
      if (problem.type === "small-tap-target") bucket.taps += 1;
      if (problem.type === "outside-viewport") bucket.outside += 1;
      if (problem.type === "low-contrast") bucket.contrast += 1;
    }
  }
  console.log("== per width ==");
  for (const [width, bucket] of Object.entries(byWidth)) {
    console.log(`  ${width}px  tiny=${bucket.tiny}  taps=${bucket.taps}  outside=${bucket.outside}`
      + `  contrast=${bucket.contrast}  overflowViews=${bucket.overflow}`);
  }
  console.log(`totals ${JSON.stringify(counts)}`);

  // Group offenders so the fixes are obvious: which selector misbehaves in how many views.
  const grouped = {};
  for (const [key, value] of Object.entries(report.views)) {
    for (const problem of value.problems) {
      const bucket = grouped[problem.type] || (grouped[problem.type] = {});
      const name = problem.node.replace(/ "[^"]*"$/, "");
      const entry = bucket[name] || (bucket[name] = { views: new Set(), sample: problem });
      entry.views.add(key);
    }
  }
  const worst = {};
  for (const [type, bucket] of Object.entries(grouped)) {
    worst[type] = Object.entries(bucket)
      .map(([name, entry]) => ({ name, views: entry.views.size, sample: entry.sample }))
      .sort((left, right) => right.views - left.views)
      .slice(0, 25);
  }
  await fs.writeFile(path.join(output, "offenders.json"), JSON.stringify(worst, null, 2));
  for (const [type, rows] of Object.entries(worst)) {
    console.log(`\n== ${type} ==`);
    for (const row of rows) console.log(`  ${String(row.views).padStart(3)} views  ${row.name}`);
  }

  const touchWidths = WIDTHS.filter((width) => width <= 900);
  const regressions = touchWidths.filter((width) => {
    const bucket = byWidth[width];
    return bucket && (bucket.tiny > 0 || bucket.taps > 0 || bucket.outside > 0 || bucket.overflow > 0
      || bucket.contrast > 0);
  });  if (counts.errors > 0) {
    console.error("\n页面脚本报错，见 report.json 的 errors-* 字段");
    process.exitCode = 1;
  } else if (regressions.length) {
    console.error(`\n手机/平板宽度仍有问题：${regressions.join(", ")}px`);
    process.exitCode = 1;
  } else {
    console.log("\n手机与平板宽度全部通过");
  }
} finally {
  await browser.close();
  await stub.close();
}
