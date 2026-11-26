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
const systemChrome = ["/usr/bin/google-chrome", "/usr/bin/chromium", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"];
const exe =
  process.env.CHROME_PATH ||
  findExecutable(path.join(scriptDir, "node_modules", ".remotion", "chrome-headless-shell")) ||
  systemChrome.find((p) => fs.existsSync(p));
if (!exe) {
  console.error("Не найден Chrome. Выполните: cd video && npx remotion browser ensure");
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
      // ролик браузер читает частями и обрывает первый запрос (net::ERR_ABORTED) — это не ошибка; сам ролик проверяется ниже
      if (/\.(mp4|webm)$/i.test(req.url()) && req.failure()?.errorText === "net::ERR_ABORTED") return;
      failedRequests.push(`${req.url()} (${req.failure()?.errorText})`);
    });

    await page.goto(pathToFileURL(htmlPath).href, { waitUntil: "load" });
    await page.selectOption("#theme-select", vp.theme);
    await page.waitForTimeout(100);

    // Каждая вкладка и каждый шаг: прокрутка, кегль, картинки, формулы
    const bodyFont = await page.evaluate(() => parseFloat(getComputedStyle(document.body).fontSize));
    const layoutMetrics = { scrollWidth: 0, innerWidth: vp.width, bodyFont };
    const problems = await page.evaluate(async () => {
      const out = [];
      const imgs = Array.from(document.querySelectorAll("img"));
      await Promise.all(imgs.map((im) => im.decode().catch(() => out.push("картинка не загрузилась: " + im.getAttribute("src")))));
      const katexErr = document.querySelectorAll(".katex-error");
      katexErr.forEach((k) => out.push("формула не разобрана: " + (k.getAttribute("title") || k.textContent).slice(0, 160)));
      if (document.querySelector('.math-tex:not(:has(.katex))')) out.push("есть формулы без отрисовки: " + document.querySelector('.math-tex:not(:has(.katex))').textContent.slice(0, 120));
      const check = (where) => {
        const de = document.documentElement;
        if (de.scrollWidth > window.innerWidth) {
          const wide = Array.from(document.querySelectorAll("main *")).find((e) => {
            const r = e.getBoundingClientRect();
            return r.width && r.right > window.innerWidth + 1 && !e.closest(".table-scroll, .math-tex");
          });
          out.push(where + ": горизонтальная прокрутка " + de.scrollWidth + " > " + window.innerWidth + (wide ? " из-за " + wide.tagName + "." + wide.className : ""));
        }
        const walker = document.createTreeWalker(document.querySelector("main"), NodeFilter.SHOW_TEXT);
        let small = null;
        while (walker.nextNode()) {
          const el = walker.currentNode.parentElement;
          if (!walker.currentNode.textContent.trim() || !el.offsetParent || el.closest(".katex, .sr-only")) continue;
          if (parseFloat(getComputedStyle(el).fontSize) < 16) { small = el; break; }
        }
        if (small) out.push(where + ": кегль меньше 16 px у " + small.tagName + "." + small.className);
      };
      const steps = Array.from(document.querySelectorAll(".step-section"));
      const was = steps.map((x) => x.hidden);
      for (const st of steps) {
        steps.forEach((x) => { x.hidden = x !== st; });
        st.querySelectorAll("details").forEach((d) => { d.open = true; });
        check(st.id);
        st.querySelectorAll("details").forEach((d) => { d.open = false; });
      }
      steps.forEach((x, i) => { x.hidden = was[i]; });
      const first = document.querySelector('.tab-btn[data-tab="step"]');
      for (const b of document.querySelectorAll(".tab-btn[data-tab]")) { b.click(); check("вкладка " + b.dataset.tab); }
      first.click();
      return out;
    });
    // Ролики курса: файл на месте, кодек читается, длительность есть, субтитры подключены
    const videoProblems = await page.evaluate(async () => {
      const vs = Array.from(document.querySelectorAll("video"));
      vs.forEach((v) => { v.preload = "auto"; v.load(); });
      const ready = (v) => new Promise((res) => {
        if (v.readyState >= 1 || v.error) return res();
        v.addEventListener("loadedmetadata", res, { once: true });
        v.addEventListener("error", res, { once: true });
        setTimeout(res, 8000);
      });
      await Promise.all(vs.map(ready));
      return vs.flatMap((v) => {
        const name = (v.currentSrc || v.src || "").split("/").pop();
        if (v.error) return [`ролик ${name}: ошибка ${v.error.code}`];
        if (v.readyState < 1 || !(v.duration > 0)) return [`ролик ${name}: не загрузился`];
        if (!v.textTracks.length) return [`ролик ${name}: нет субтитров`];
        return [];
      });
    });
    const videoCount = await page.evaluate(() => document.querySelectorAll("video").length);
    if (videoProblems.length) problems.push(...videoProblems);
    if (problems.length) throw new Error(`[${vp.name}] ` + problems.slice(0, 12).join("; "));
    layoutMetrics.videos = videoCount;
    layoutMetrics.scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
    if (bodyFont < 16) problems.push(`шрифт меньше 16 px: ${bodyFont}`);
    if (problems.length) throw new Error(`[${vp.name}] ` + problems.slice(0, 12).join("; "));

    // Проверка интерактива на мобильном и десктопе: задания, карточки, карта, конспект, Volve
    if (vp.name === "desktop_1280") {
      // Отвечаем на все задания: открываем шаг, в котором стоит задание
      const taskCards = await page.locator(".task-card").all();
      for (const tc of taskCards) {
        const stepId = await tc.evaluate((el) => el.closest(".step-section").id.replace(/^step-/, ""));
        if (!(await tc.isVisible())) await page.evaluate((id) => document.querySelector(`.path-item[data-step="${id}"]`).click(), stepId);
        const radio = tc.locator('input[type="radio"]').first();
        const check = tc.locator('input[type="checkbox"]').first();
        const gap = tc.locator(".gap-input").first();
        const calc = tc.locator(".calc-input").first();
        const moveUp = tc.locator('[data-move="up"]').nth(1);
        if (await radio.count()) await radio.check();
        else if (await check.count()) await check.check();
        else if (await gap.count()) for (const g of await tc.locator(".gap-input").all()) await g.fill("неколлектор");
        else if (await calc.count()) await calc.fill("11,4");
        else if (await moveUp.count()) await moveUp.click();
        await tc.locator(".check-task-btn").click();
        const fbVisible = await tc.locator(".task-feedback").isVisible();
        if (!fbVisible) throw new Error("Обратная связь задания не появилась");
      }

      // Проверка Volve: виджет стоит на одном из шагов
      const volveRadio = page.locator('input[name="volve-pick"][value="A"]').first();
      if (await volveRadio.count()) {
        const vs = await volveRadio.evaluate((el) => el.closest(".step-section").id.replace(/^step-/, ""));
        await page.evaluate((id) => document.querySelector(`.path-item[data-step="${id}"]`).click(), vs);
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
      `QA OK [${vp.name}] ${vp.width}x${vp.height} (${vp.theme}): scrollWidth=${layoutMetrics.scrollWidth}<=${layoutMetrics.innerWidth}, font=${layoutMetrics.bodyFont}px, роликов=${layoutMetrics.videos}, offline=true`
    );
    await context.close();
  }
} finally {
  await browser.close();
}
