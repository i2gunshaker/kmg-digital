// Снимает PNG-кадры ролика с HTML-сцен: node video/render_scenes.mjs <jobs.json>
// jobs.json: [{html, png, width, height}]. Сцены открываются по file:// без сети, как офлайн-курс.
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
    } else if (entry.name === "chrome-headless-shell" || entry.name === "chrome") {
      return full;
    }
  }
  return null;
}

const jobsPath = process.argv[2];
if (!jobsPath) {
  console.error("Usage: node video/render_scenes.mjs <jobs.json>");
  process.exit(2);
}
const jobs = JSON.parse(fs.readFileSync(jobsPath, "utf-8"));
const scriptDir = path.dirname(new URL(import.meta.url).pathname);
const systemChrome = ["/usr/bin/google-chrome", "/usr/bin/chromium", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"];
const exe =
  process.env.CHROME_PATH ||
  findExecutable(path.join(scriptDir, "node_modules", ".remotion", "chrome-headless-shell")) ||
  findExecutable(path.join(process.env.HOME || "", ".cache", "ms-playwright")) ||
  systemChrome.find((p) => fs.existsSync(p));
if (!exe) {
  console.error("Не найден Chrome: задайте CHROME_PATH или выполните npx playwright install chromium");
  process.exit(2);
}

const browser = await chromium.launch({ executablePath: exe, headless: true });
try {
  for (const job of jobs) {
    const context = await browser.newContext({ viewport: { width: job.width, height: job.height }, deviceScaleFactor: 1, offline: true });
    const page = await context.newPage();
    await page.goto(pathToFileURL(job.html).href, { waitUntil: "load" });
    await page.waitForFunction(() => document.body.dataset.ready === "1", null, { timeout: 10000 }).catch(() => {});
    await page.evaluate(() => Promise.all(Array.from(document.images).map((im) => (im.complete ? 0 : new Promise((r) => (im.onload = im.onerror = r))))));
    await page.screenshot({ path: job.png, fullPage: false });
    await context.close();
    console.log("кадр", path.basename(job.png));
  }
} finally {
  await browser.close();
}
