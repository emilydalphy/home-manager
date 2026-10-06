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
//      - push notifications are switched on in the Xcode project: the
//        entitlements file says aps-environment and both build settings
//        point at it (without it, iOS never hands the app a device token
//        and no notification can ever arrive)
//      - Apple's privacy manifest (ios/App/App/PrivacyInfo.xcprivacy) is
//        there, ships with the app, says it tracks nobody, and declares
//        every "required reason" API that our native code or a Capacitor
//        plugin in node_modules calls (Apple rejects the build otherwise)
//
// `npm run check` runs only the checks (for CI): it fails instead of
// writing when www/app-config.js is out of date.

import { readFileSync, writeFileSync, existsSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');

// Apple's "required reason" APIs, by the names native code calls them.
// Not exhaustive — the five categories as Apple lists them, by their
// commonest spellings — but enough that a plugin upgrade which starts
// calling one stops the build here instead of at App Review.
const REQUIRED_REASON_APIS = [
  ['NSPrivacyAccessedAPICategoryUserDefaults', /\bUserDefaults\b|\bNSUserDefaults\b/],
  ['NSPrivacyAccessedAPICategoryFileTimestamp', /\bNSFileCreationDate\b|\bNSFileModificationDate\b|\bcreationDateKey\b|\bcontentModificationDateKey\b|\bgetattrlist\b/],
  ['NSPrivacyAccessedAPICategorySystemBootTime', /\bsystemUptime\b|\bmach_absolute_time\b/],
  ['NSPrivacyAccessedAPICategoryDiskSpace', /\bNSFileSystemFreeSize\b|\bNSFileSystemSize\b|\bvolumeAvailableCapacity\w*Key\b|\bstatfs\b/],
  ['NSPrivacyAccessedAPICategoryActiveKeyboards', /\bactiveInputModes\b/],
];

// The first native source file under `dir` matching `pattern`, or null.
// Tests are skipped: they never ship in the app.
function findUse(dir, pattern) {
  if (!existsSync(dir)) return null;
  for (const name of readdirSync(dir)) {
    if (name === 'Tests' || name === 'node_modules' || name.startsWith('.')) continue;
    const path = join(dir, name);
    if (statSync(path).isDirectory()) {
      const hit = findUse(path, pattern);
      if (hit) return hit;
    } else if (/\.(swift|m|mm|h|c)$/.test(name) && pattern.test(readFileSync(path, 'utf8'))) {
      return path;
    }
  }
  return null;
}
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

// 3. Push notifications: the capability, once the Xcode project exists.
if (existsSync(pbxproj)) {
  const text = readFileSync(pbxproj, 'utf8');
  const signed = [...text.matchAll(/CODE_SIGN_ENTITLEMENTS = ([^;]+);/g)].map((m) => m[1].trim());
  if (signed.length < 2 || signed.some((v) => v !== 'App/App.entitlements')) {
    problems.push('ios/App/App.xcodeproj: CODE_SIGN_ENTITLEMENTS should be App/App.entitlements for Debug and Release (push notifications need it).');
  }
  const entitlements = join(root, 'ios', 'App', 'App', 'App.entitlements');
  const ent = existsSync(entitlements) ? readFileSync(entitlements, 'utf8') : '';
  if (!/<key>aps-environment<\/key>\s*<string>(development|production)<\/string>/.test(ent)) {
    problems.push('ios/App/App/App.entitlements: should set aps-environment (the Push Notifications capability).');
  }
}

// 4. Apple's privacy manifest. An upload without one is turned away, and
//    one that leaves out a "required reason" API the code calls gets the
//    build rejected after review — so check both here, where the message
//    can be plain, rather than in an email from Apple days later.
const manifest = join(root, 'ios', 'App', 'App', 'PrivacyInfo.xcprivacy');
if (existsSync(pbxproj)) {
  if (!existsSync(manifest)) {
    problems.push('ios/App/App/PrivacyInfo.xcprivacy is missing: Apple refuses an upload without it (see README, "Privacy manifest").');
  } else {
    const text = readFileSync(manifest, 'utf8');
    if (!/<key>NSPrivacyTracking<\/key>\s*<false\/>/.test(text)) {
      problems.push('ios/App/App/PrivacyInfo.xcprivacy: NSPrivacyTracking should be false (Pomona tracks nobody).');
    }
    if (!readFileSync(pbxproj, 'utf8').includes('PrivacyInfo.xcprivacy in Resources')) {
      problems.push('ios/App/App.xcodeproj: PrivacyInfo.xcprivacy is not in the app\'s Resources, so it would not ship.');
    }
    const declared = new Set([...text.matchAll(/<string>(NSPrivacyAccessedAPICategory\w+)<\/string>/g)].map((m) => m[1]));
    // The native code that ships in the app: ours, Capacitor's, the plugins'.
    const sources = [join(root, 'ios', 'App', 'App')];
    const capDir = join(root, 'node_modules', '@capacitor');
    if (existsSync(capDir)) {
      for (const name of readdirSync(capDir)) {
        if (name === 'cli') continue;
        sources.push(join(capDir, name));
      }
    }
    for (const [category, pattern] of REQUIRED_REASON_APIS) {
      if (declared.has(category)) continue;
      const hit = sources.map((dir) => findUse(dir, pattern)).find(Boolean);
      if (hit) {
        problems.push(`${relative(root, hit)} uses an API Apple asks a reason for (${category}), and PrivacyInfo.xcprivacy doesn't declare it. Add it with its reason code.`);
      }
    }
  }
}

if (problems.length) {
  console.error('The app settings disagree:\n  - ' + problems.join('\n  - '));
  process.exit(1);
}
console.log(`App settings agree: ${serverUrl.origin}, version ${version}.`);
