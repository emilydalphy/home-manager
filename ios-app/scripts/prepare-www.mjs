// Keeps the app's few settings in step, so each is written down once.
//
//   capacitor.config.json  server.url  -> where the app opens (the ONE place
//                                         the live address is written)
//   package.json           version     -> the app version people see
//
// What this does:
//   1. Writes www/app-config.js, which the bundled offline page reads to
//      know where "Try again" goes.
//   2. Checks the rest agrees, and stops with a plain message if not:
//      - ios.appendUserAgent is exactly "PomonaApp/<version>"
//      - the Xcode project's MARKETING_VERSION is <version>
//      - Info.plist's WKAppBoundDomains lists the server's host and
//        "localhost" (without both, the service worker and the offline page
//        stop working inside the app)
//
// `npm run check` runs only the checks (for CI): it fails instead of
// writing when www/app-config.js is out of date.

import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const checkOnly = process.argv.includes('--check');
const problems = [];

const config = JSON.parse(readFileSync(join(root, 'capacitor.config.json'), 'utf8'));
const pkg = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'));
const version = pkg.version;

let serverUrl;
try {
  serverUrl = new URL(config.server && config.server.url);
} catch (err) {
  problems.push('capacitor.config.json: server.url is missing or not a web address.');
}
if (serverUrl && serverUrl.protocol !== 'https:') {
  problems.push(`capacitor.config.json: server.url must be https (it is ${serverUrl.protocol}).`);
}

const expectedAgent = `PomonaApp/${version}`;
if (!config.ios || config.ios.appendUserAgent !== expectedAgent) {
  problems.push(`capacitor.config.json: ios.appendUserAgent should be "${expectedAgent}" to match package.json's version.`);
}

// 1. www/app-config.js
if (serverUrl) {
  const body =
    '// Written by scripts/prepare-www.mjs from capacitor.config.json -- do not edit.\n' +
    `window.POMONA_APP = ${JSON.stringify({ serverUrl: serverUrl.origin + '/', version })};\n`;
  const target = join(root, 'www', 'app-config.js');
  const current = existsSync(target) ? readFileSync(target, 'utf8') : '';
  if (current !== body) {
    if (checkOnly) problems.push('www/app-config.js is out of date: run `npm run prepare-www`.');
    else writeFileSync(target, body);
  }
}

// 2. The Xcode project, once it exists (npx cap add ios made it).
const pbxproj = join(root, 'ios', 'App', 'App.xcodeproj', 'project.pbxproj');
if (existsSync(pbxproj)) {
  const text = readFileSync(pbxproj, 'utf8');
  const versions = [...text.matchAll(/MARKETING_VERSION = ([^;]+);/g)].map((m) => m[1].trim());
  if (!versions.length || versions.some((v) => v !== version)) {
    problems.push(`ios/App/App.xcodeproj: MARKETING_VERSION should be ${version} everywhere (found ${versions.join(', ') || 'none'}).`);
  }
}

const plist = join(root, 'ios', 'App', 'App', 'Info.plist');
if (existsSync(plist) && serverUrl) {
  const text = readFileSync(plist, 'utf8');
  const block = text.match(/<key>WKAppBoundDomains<\/key>\s*<array>([\s\S]*?)<\/array>/);
  const listed = block ? [...block[1].matchAll(/<string>([^<]+)<\/string>/g)].map((m) => m[1].trim()) : [];
  for (const host of [serverUrl.hostname, 'localhost']) {
    if (!listed.includes(host)) {
      problems.push(`ios/App/App/Info.plist: WKAppBoundDomains should list "${host}".`);
    }
  }
}

if (problems.length) {
  console.error('The app settings disagree:\n  - ' + problems.join('\n  - '));
  process.exit(1);
}
console.log(`App settings agree: ${serverUrl.origin}, version ${version}.`);
