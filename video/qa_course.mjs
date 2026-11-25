import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { chromium } from "playwright-core";

function findExecutable(dir) {
  if (!fs.existsSync(dir)) return null;
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      const hit = findExecutable(full);
      if (hit) return hit;
    } else if (entry.name === "chrome-headless-shell") {
      return full;
    }
  }
  return null;
}

const htmlArg = process.argv[2];
if (!htmlArg) {
  console.error("Usage: node video/qa_course.mjs <path/to/index.html>");
  process.exit(2);
}

const htmlPath = path.resolve(htmlArg);
if (!fs.existsSync(htmlPath)) {
  console.error("File not found:", htmlPath);
  process.exit(2);
}

const scriptDir = path.dirname(new URL(import.meta.url).pathname);
const exe = findExecutable(path.join(scriptDir, "node_modules", ".remotion", "chrome-headless-shell"));
if (!exe) {
  console.error("chrome-headless-shell not found. Run: cd video && npx remotion browser ensure");
  process.exit(2);
}

const qaDir = path.resolve("build", "qa");
fs.mkdirSync(qaDir, { recursive: true });

const browser = await chromium.launch({ executablePath: exe, headless: true });

try {
  const viewports = [
    { name: "mobile_390", width: 390, height: 844, theme: "dark" },
    { name: "desktop_1280", width: 1280, height: 800, theme: "light" },
  ];

  for (const vp of viewports) {
    const context = await browser.newContext({
      viewport: { width: vp.width, height: vp.height },
      offline: true,
    });
    const page = await context.newPage();
    const consoleErrors = [];
    const failedRequests = [];
    const externalRequests = [];

    page.on("pageerror", (err) => consoleErrors.push(`pageerror: ${err.message}`));
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(`console.error: ${msg.text()}`);
    });
    page.on("request", (req) => {
      const url = req.url();
      if (url.startsWith("http://") || url.startsWith("https://")) {
        externalRequests.push(url);
      }
    });
    page.on("requestfailed", (req) => {
      failedRequests.push(`${req.url()} (${req.failure()?.errorText})`);
    });

    await page.goto(pathToFileURL(htmlPath).href, { waitUntil: "load" });
    await page.selectOption("#theme-select", vp.theme);
    await page.waitForTimeout(100);

    // Проверка горизонтальной прокрутки и минимального размера шрифта
    const layoutMetrics = await page.evaluate(() => {
      const docEl = document.documentElement;
      const bodyFont = parseFloat(getComputedStyle(document.body).fontSize);
      const imgs = Array.from(document.querySelectorAll("img"));
      const brokenImgs = imgs.filter((im) => !im.complete || im.naturalWidth === 0).map((im) => im.src);
      return {
        scrollWidth: docEl.scrollWidth,
        innerWidth: window.innerWidth,
        bodyFont,
        brokenImgs,
      };
    });

    if (layoutMetrics.scrollWidth > layoutMetrics.innerWidth) {
      throw new Error(
        `[${vp.name}] Горизонтальный скролл: scrollWidth=${layoutMetrics.scrollWidth} > innerWidth=${layoutMetrics.innerWidth}`
      );
    }
    if (layoutMetrics.bodyFont < 16) {
      throw new Error(`[${vp.name}] Шрифт меньше 16px: ${layoutMetrics.bodyFont}`);
    }
    if (layoutMetrics.brokenImgs.length > 0) {
      throw new Error(`[${vp.name}] Не загрузились картинки: ${layoutMetrics.brokenImgs.join(", ")}`);
    }

    // Проверка интерактива на мобильном и десктопе: задания, карточки, карта, конспект, Volve
    if (vp.name === "desktop_1280") {
      // Отвечаем на задания первого шага
      const taskCards = await page.locator(".task-card").all();
      for (const tc of taskCards) {
        if (!(await tc.isVisible())) continue;
        const radio = tc.locator('input[type="radio"]').first();
        const check = tc.locator('input[type="checkbox"]').first();
        const gap = tc.locator(".gap-input").first();
        const calc = tc.locator(".calc-input").first();
        const moveUp = tc.locator('[data-move="up"]').nth(1);
        if (await radio.count()) await radio.check();
        else if (await check.count()) await check.check();
        else if (await gap.count()) await gap.fill("неколлектор");
        else if (await calc.count()) await calc.fill("11,4");
        else if (await moveUp.count()) await moveUp.click();
        await tc.locator(".check-task-btn").click();
        const fbVisible = await tc.locator(".task-feedback").isVisible();
        if (!fbVisible) throw new Error("Обратная связь задания не появилась");
      }

      // Проверка Volve (если есть на видимом шаге)
      const volveRadio = page.locator('input[name="volve-pick"][value="A"]').first();
      if ((await volveRadio.count()) && (await volveRadio.isVisible())) {
        await volveRadio.check();
        await page.locator(".volve-check-btn").first().click();
        const fb = await page.locator(".volve-feedback").first().textContent();
        if (!fb || !fb.includes("Верно")) throw new Error("Volve check failed");
      }

      // Проверка вкладок: Карточки, Карта, Конспект
      await page.click('.tab-btn[data-tab="cards"]');
      await page.click("#flash-flip");
      await page.click("#flash-know");
      await page.click('.tab-btn[data-tab="map"]');
      await page.click('.tab-btn[data-tab="summary"]');
      await page.click('.tab-btn[data-tab="step"]');
    }

    await page.screenshot({ path: path.join(qaDir, `${vp.name}.png`), fullPage: false });

    if (externalRequests.length > 0) {
      throw new Error(`[${vp.name}] Сетевые запросы запрещены: ${externalRequests.join(", ")}`);
    }
    if (failedRequests.length > 0) {
      throw new Error(`[${vp.name}] Ошибки загрузки локальных ресурсов: ${failedRequests.join(", ")}`);
    }
    if (consoleErrors.length > 0) {
      throw new Error(`[${vp.name}] Ошибки консоли/JS: ${consoleErrors.join(" | ")}`);
    }

    console.log(
      `QA OK [${vp.name}] ${vp.width}x${vp.height} (${vp.theme}): scrollWidth=${layoutMetrics.scrollWidth}<=${layoutMetrics.innerWidth}, font=${layoutMetrics.bodyFont}px, offline=true`
    );
    await context.close();
  }
} finally {
  await browser.close();
}
