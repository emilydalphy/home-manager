// Takes the App Store screenshots from the demo household.
//
// Run against a THROWAWAY database only, never the real one:
//   DB_PATH=/tmp/shots.db DEMO_PASSPHRASE="maple river copper lantern" python create_demo_household.py
//   DB_PATH=/tmp/shots.db DISABLE_BACKUPS=1 DISABLE_MORNING_TEXT=1 HOME_MANAGER_PASSWORD=x \
//     SESSION_SECRET=x python -m uvicorn app.main:app --port 8104
//   node ios-app/store/take-screenshots.mjs ios-app/store/screenshots 440 956 iphone-6.9in   # 1320x2868
//   node ios-app/store/take-screenshots.mjs ios-app/store/screenshots 430 932 iphone-6.7in   # 1290x2796
// Needs Playwright and a Chromium (the paths below are this sandbox's).
// The passphrase in this file is the throwaway one above, not the live demo's.
import { chromium } from '/opt/node22/lib/node_modules/playwright/index.mjs';
import { mkdirSync } from 'node:fs';
const [,, base, w, h, label] = process.argv;
const out = `${base}/${label}`; mkdirSync(out, { recursive: true });
const b = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' });
const ctx = await b.newContext({ viewport: { width: +w, height: +h }, deviceScaleFactor: 3, isMobile: true, hasTouch: true });
const p = await ctx.newPage();
const go = async (path) => { await p.goto('http://localhost:8104' + path); await p.waitForTimeout(2500); };
await go('/login');
await p.fill('input[name=password]', 'maple river copper lantern');
await p.press('input[name=password]', 'Enter'); await p.waitForTimeout(2500);
await p.getByText('Maya').first().click(); await p.waitForTimeout(2500);
await go('/week'); await p.screenshot({ path: `${out}/1-plan.png` });
await go('/grocery');
await p.getByText('One list is fine').first().click({ timeout: 3000 }).catch(() => {});
await p.waitForTimeout(1500); await p.screenshot({ path: `${out}/2-list.png` });
await go('/kitchen');
await p.getByText('Chili').first().click(); await p.waitForTimeout(1500);
await p.getByRole('button', { name: /start cooking/i }).first().click();
await p.waitForTimeout(6000); await p.screenshot({ path: `${out}/3-cook.png` });
await go('/');
await p.screenshot({ path: `${out}/5-today.png` });
await p.click('#chat-fab'); await p.waitForTimeout(2000);
await p.locator('textarea:visible').first().fill('Can we swap Friday for something quicker?');
await p.waitForTimeout(800); await p.screenshot({ path: `${out}/4-chat.png` });
await b.close();
