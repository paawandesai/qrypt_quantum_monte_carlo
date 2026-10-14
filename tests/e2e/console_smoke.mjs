// Console smoke test (Playwright): connect, submit the standard case, see it
// COMPLETE with the Black-Scholes check, replay it and see "bit-identical",
// then a failure path (fault-injected shard -> PARTIAL) and a 401.
//
//   python tests/e2e/local_server.py 8765 &          # local, moto-backed
//   BASE_URL=http://127.0.0.1:8765 QMC_KEY=local-smoke-bearer node tests/e2e/console_smoke.mjs
//
// Against a deployment: BASE_URL=<function url> QMC_KEY=<bearer key> (spends 1 KiB of entropy per submit).
import { chromium } from "playwright";

const BASE = process.env.BASE_URL || "http://127.0.0.1:8765";
const KEY = process.env.QMC_KEY;
const SHOTS = process.env.SHOTS_DIR;
const FAULT = process.env.SKIP_FAULT !== "1";
if (!KEY) throw new Error("set QMC_KEY");

const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
const step = (m) => console.log(`✓ ${m}`);
const shot = async (name) => { if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true }); };

await page.goto(BASE + "/");
await page.getByText("Demo — synthetic data only").waitFor();
const robots = await page.locator('meta[name="robots"]').getAttribute("content");
if (!robots.includes("noindex")) throw new Error("noindex missing");
step("banner and noindex present");

await page.fill("#key", "wrong-key");
await page.click("form button[type=submit]");
await page.getByText("401 unauthorized").waitFor();
step("wrong key shows 401");

await page.fill("#key", KEY);
await page.click("form button[type=submit]");
await page.getByRole("heading", { name: "Jobs" }).waitFor();
step("connected");
await shot("jobs-empty");

await page.click("nav >> text=New job");
await page.getByText("Black-Scholes reference 10.45").waitFor();
await shot("new-job");
await page.fill("#f-sigma", "-1");
await page.getByText("sigma must be greater than 0").waitFor();
if (!(await page.locator("button[type=submit]").isDisabled())) throw new Error("submit should be disabled");
await page.fill("#f-sigma", "0.2");
step("client-side validation mirrors the server");

await page.click("button:has-text('Submit job')");
await page.waitForURL(/#\/job\//);
await page.getByText("COMPLETE").first().waitFor({ timeout: 120000 });
await page.getByText("within 3 SE").waitFor();
const jobUrl = page.url();
step("standard job COMPLETE within 3 SE");
await page.click("summary:has-text('Provenance')");
await page.getByText("not provided by Qrypt").waitFor();
await shot("job-complete");

page.once("dialog", (d) => d.accept());
await page.click("button:has-text('Replay')");
await page.waitForURL((u) => u.toString() !== jobUrl);
await page.getByText("✓ bit-identical").waitFor({ timeout: 120000 });
step("replay is bit-identical");
await shot("replay");

if (FAULT) {
  await page.click("nav >> text=New job");
  await page.fill("#f-paths", "20000");
  await page.fill("#f-shards", "4");
  await page.fill("#f-fail", "2");
  await page.click("button:has-text('Submit job')");
  await page.waitForURL(/#\/job\//);
  await page.getByText("This job is PARTIAL").waitFor({ timeout: 300000 });
  await page.getByText("Failed shard indexes").waitFor();
  step("fault-injected shard ends PARTIAL");
  await shot("partial");
}

await page.click("nav >> text=Jobs");
await page.locator("tbody tr").first().waitFor();
await page.click("nav >> text=System");
await page.getByText("Entropy used").waitFor();
await shot("system");
step("jobs list and system view");

await page.setViewportSize({ width: 390, height: 844 });
const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
if (overflow) throw new Error("horizontal scroll at 390px");
step("no horizontal scroll at 390px");

if (errors.length) throw new Error("page errors: " + errors.join("; "));
await browser.close();
console.log("console smoke test passed");
