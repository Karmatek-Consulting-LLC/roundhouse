// Capture every notable Roundhouse view to PNG, in dark and light themes.
//
// Drives a headless Chromium against a running stack at BASE (default
// http://localhost:3080). Logs in via the API, injects the resulting token
// (and the chosen theme) into localStorage, then walks a script of routes,
// pausing for network idle + UI animations before screenshotting.
//
// Usage:
//   node docs/capture/capture.mjs --theme dark
//   node docs/capture/capture.mjs --theme light
//   node docs/capture/capture.mjs --theme both    (default)
//
// Assumes docs/capture/seed_demo.py has already run, so the Taggart
// Transcontinental demo servers exist in the stack.

import { chromium } from "playwright";
import { mkdir, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "..", "..");
const SHOTS_DIR = path.join(REPO_ROOT, "docs", "screenshots");

const args = parseArgs(process.argv.slice(2));
const BASE = args.base ?? "http://localhost:3080";
const EMAIL = args.email ?? "admin@mcp.local";
const PASSWORD = args.password ?? "admin";
const THEME = args.theme ?? "both"; // "dark" | "light" | "both"
// --only <prefix[,prefix...]> re-captures just the matching steps (by name
// prefix), e.g. --only 02-dashboard,21-editor-usage. Useful for iterating on
// a single shot without redriving all ~28 routes.
const ONLY = args.only ? String(args.only).split(",").map((s) => s.trim()).filter(Boolean) : null;
const stepMatchesOnly = (name) => !ONLY || ONLY.some((p) => name.startsWith(p));
const VIEWPORT = { width: 1600, height: 1050 };
// fullPage=true captures the entire scrollable height of each route so
// content that runs past the viewport (charts, primitives list, audit log)
// is visible end-to-end in the docs.
const FULL_PAGE = true;

// The set of routes we walk. The `wait` field lets each step express its
// own readiness condition (e.g. wait for a specific selector or a chart to
// render). `prep` runs in-page before the screenshot — useful for opening
// dialogs or hovering a control.
//
// Server names match those seeded by docs/capture/seed_demo.py.
const STEPS = [
  // Dashboard — live charts + an SSE stream keep the network busy, so
  // networkidle never fires (same as the logs tail below). Gate on DOM +
  // the dashboard heading, then settle for the chart paint.
  { name: "02-dashboard",        url: "/",                                                              waitUntil: "domcontentloaded", wait: { selector: 'text=/dashboard|servers/i', delay: 1800 } },

  // Servers list
  { name: "03-servers-list",     url: "/servers",                                                       wait: { selector: "table", delay: 600 } },

  // Create-server dialog (4 tabs: Structured | Code-first | From Git | Import)
  { name: "04-create-structured", url: "/servers",                                                      prep: openCreateDialogTab("structured"),                 wait: { selector: 'role=dialog', delay: 400 } },
  { name: "05-create-code",       url: "/servers",                                                      prep: openCreateDialogTab("code"),                       wait: { selector: 'role=dialog', delay: 400 } },
  { name: "06-create-from-git",   url: "/servers",                                                      prep: openCreateDialogTab("git"),                        wait: { selector: 'role=dialog', delay: 400 } },
  { name: "07-create-import",     url: "/servers",                                                      prep: openCreateDialogTab("import"),                     wait: { selector: 'role=dialog', delay: 400 } },

  // Server editor — dispatch (flagship structured server)
  { name: "10-editor-overview",  url: "/servers/dispatch",                              wait: { selector: 'text=/overview|dispatch/i', delay: 700 } },
  { name: "11-editor-primitive-tool",   url: "/servers/dispatch/primitives/schedule_train", wait: { selector: 'textarea, .cm-editor', delay: 900 } },
  { name: "12-editor-primitive-resource", url: "/servers/dispatch/primitives/track_map",   wait: { selector: 'textarea, .cm-editor', delay: 900 } },
  { name: "13-editor-primitive-prompt", url: "/servers/dispatch/primitives/morning_briefing", wait: { selector: 'textarea, .cm-editor', delay: 900 } },
  { name: "14-editor-primitive-new",   url: "/servers/dispatch/primitives:new",          wait: { selector: 'text=/new primitive/i', delay: 500 } },
  { name: "15-editor-imports",   url: "/servers/dispatch/imports",                       wait: { selector: 'text=/imports|globals/i', delay: 500 } },
  // track-maintenance carries the pip dependency (numpy) in the seed.
  { name: "16-editor-packages",  url: "/servers/track-maintenance/packages",             wait: { selector: 'text=/pip|packages/i', delay: 500 } },
  { name: "17-editor-apt",       url: "/servers/dispatch/apt-packages",                  wait: { selector: 'text=/apt|os/i', delay: 500 } },
  { name: "18-editor-env",       url: "/servers/dispatch/env",                           wait: { selector: 'text=/environment|env/i', delay: 500 } },
  { name: "19-editor-auth",      url: "/servers/dispatch/auth",                          wait: { selector: 'text=/tokens|auth/i', delay: 500 } },
  { name: "20-editor-assets",    url: "/servers/dispatch/assets",                        wait: { selector: 'text=/assets|upload/i', delay: 500 } },
  { name: "21-editor-usage",     url: "/servers/dispatch/usage",                         wait: { selector: 'text=/metrics|calls|usage/i', delay: 1500 } },
  // crew-scheduling carries the heaviest traffic in the seed, so its usage page
  // shows the most chart variety + token attribution.
  { name: "21a-editor-usage-busy", url: "/servers/crew-scheduling/usage",                                     wait: { selector: 'text=/metrics|calls|usage/i', delay: 1500 } },

  // Logs tab — crew-scheduling has LOG_LEVEL=DEBUG so the dropdown shows it.
  // Logs page opens an SSE stream so networkidle would never fire. Tail can
  // be very long; cap at viewport to keep the shot scannable.
  { name: "22-editor-logs",      url: "/servers/crew-scheduling/logs",                                       fullPage: false, waitUntil: "domcontentloaded", wait: { selector: 'pre', delay: 2400 } },

  // Code-mode editor — signal-telemetry
  { name: "30-editor-source",    url: "/servers/signal-telemetry/source",                                       wait: { selector: '.cm-editor', delay: 1500 } },
  { name: "31-editor-source-env", url: "/servers/signal-telemetry/env",                                         wait: { selector: 'text=/environment/i', delay: 500 } },

  // Stopped server (gray status badge prominent)
  { name: "40-editor-stopped",   url: "/servers/freight-billing",                                        wait: { selector: 'text=/stopped|exited|overview/i', delay: 700 } },

  // Platform admin areas
  { name: "50-settings",         url: "/settings",                                                       wait: { selector: 'text=/settings|hostname/i', delay: 600 } },
  { name: "51-users",            url: "/users",                                                          wait: { selector: 'text=/users|email/i', delay: 600 } },
  { name: "52-teams",            url: "/teams",                                                          wait: { selector: 'text=/teams|members/i', delay: 600 } },
  // Audit log can be hundreds of rows long; cap at viewport so the shot
  // doesn't become a 20k-pixel scroll-roll.
  { name: "53-audit",            url: "/audit",                                                          fullPage: false, wait: { selector: 'text=/audit|action/i', delay: 800 } },
];

async function main() {
  const themes = THEME === "both" ? ["dark", "light"] : [THEME];
  // Token is theme-independent; fetch once.
  const token = await loginViaApi(BASE, EMAIL, PASSWORD);

  for (const theme of themes) {
    console.log(`\n=== Capturing ${theme} theme ===`);
    const outDir = path.join(SHOTS_DIR, theme);
    if (!existsSync(outDir)) await mkdir(outDir, { recursive: true });
    await captureTheme({ theme, outDir, token });
  }

  console.log(`\nDone. Output: ${SHOTS_DIR}`);
}

async function captureTheme({ theme, outDir, token }) {
  const browser = await chromium.launch();
  const context = await browser.newContext({
    viewport: VIEWPORT,
    deviceScaleFactor: 2, // crisp retina-ish output
    colorScheme: theme,
  });
  // Seed localStorage with token + theme BEFORE the SPA boots so it doesn't
  // bounce us to /login or flash the wrong theme.
  await context.addInitScript(
    ({ tok, thm }) => {
      try {
        localStorage.setItem("token", tok);
        localStorage.setItem("theme", thm);
      } catch {}
    },
    { tok: token, thm: theme },
  );

  const page = await context.newPage();

  for (const step of STEPS) {
    if (!stepMatchesOnly(step.name)) continue;
    try {
      const url = BASE + step.url;
      console.log(`  → ${step.name}: ${step.url}`);
      // Pages that open a persistent SSE stream (e.g. /logs) never reach
      // networkidle — DOM-loaded is the right gate for them.
      const waitUntil = step.waitUntil ?? "networkidle";
      await page.goto(url, { waitUntil, timeout: 20_000 });
      if (step.prep) await step.prep(page);
      await waitForStep(page, step.wait);
      const out = path.join(outDir, `${step.name}.png`);
      // Per-step override wins. Otherwise fullPage everywhere except when
      // a `prep` opens a modal (fullPage would tile the dimmed page behind).
      const useFullPage = step.fullPage ?? (FULL_PAGE && !step.prep);
      await page.screenshot({ path: out, fullPage: useFullPage });
    } catch (err) {
      console.error(`    ! ${step.name} failed: ${err.message}`);
    }
  }

  // Anonymous pass for /login — fresh context with no token. Keeping it
  // separate sidesteps the trap where addInitScript() runs on every
  // navigation and silently wipes the token for the rest of the run.
  if (stepMatchesOnly("01-login")) await captureLogin(browser, theme, outDir);

  await browser.close();
}

async function captureLogin(browser, theme, outDir) {
  // The login page is a small centered card on a large empty canvas. At the
  // full 1600x1050 viewport the card is lost in dark space and reads as a
  // broken/empty image in the docs. Use a tighter viewport so the card is the
  // subject while keeping a little breathing room around it.
  const ctx = await browser.newContext({
    viewport: { width: 1040, height: 720 },
    deviceScaleFactor: 2,
    colorScheme: theme,
  });
  await ctx.addInitScript((thm) => {
    try { localStorage.setItem("theme", thm); } catch {}
  }, theme);
  const page = await ctx.newPage();
  try {
    console.log(`  → 01-login: /login`);
    await page.goto(BASE + "/login", { waitUntil: "networkidle", timeout: 20_000 });
    try { await page.locator('input[type="email"]').first().waitFor({ timeout: 4000 }); } catch {}
    await page.waitForTimeout(400);
    await page.screenshot({ path: path.join(outDir, "01-login.png"), fullPage: false });
  } catch (err) {
    console.error(`    ! 01-login failed: ${err.message}`);
  }
  await ctx.close();
}

async function waitForStep(page, w) {
  if (!w) return;
  if (w.selector) {
    try { await page.locator(w.selector).first().waitFor({ timeout: 4000 }); } catch {}
  }
  if (w.delay) await page.waitForTimeout(w.delay);
}

// -------- Create-server dialog helpers --------

async function clickCreate(page) {
  for (let i = 0; i < 3; i++) {
    const btn = page.locator('button:has-text("Create")').first();
    if (await btn.count()) {
      try { await btn.click({ timeout: 1500 }); break; } catch {}
    }
    await page.waitForTimeout(400);
  }
}

async function openCreateDialog(page) {
  await clickCreate(page);
}

function openCreateDialogTab(tab) {
  return async (page) => {
    await clickCreate(page);
    // Exact labels match TabsTrigger text in create-server-dialog.tsx.
    const labels = {
      structured: /^Structured$/,
      code: /^Code-first$/,
      git: /^From Git$/,
      import: /^Import$/,
    };
    try {
      await page.getByRole("tab", { name: labels[tab] }).first().click({ timeout: 1500 });
    } catch {
      try { await page.getByText(labels[tab]).first().click({ timeout: 1500 }); } catch {}
    }
    await page.waitForTimeout(300);
  };
}

// -------- API + arg parsing --------

async function loginViaApi(base, email, password) {
  // Native fetch (Node 18+). Surface errors loudly so we don't capture an
  // anonymous run thinking we were logged in.
  const res = await fetch(`${base}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) {
    throw new Error(`Login failed: ${res.status} ${await res.text()}`);
  }
  const data = await res.json();
  return data.access_token;
}

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith("--")) {
      const key = a.slice(2);
      const next = argv[i + 1];
      if (next === undefined || next.startsWith("--")) out[key] = true;
      else { out[key] = next; i++; }
    }
  }
  return out;
}

main().catch((e) => { console.error(e); process.exit(1); });
