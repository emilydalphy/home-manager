# Pomona for iPhone

## What this is

The iPhone app is a thin shell around the live Pomona website. When someone
opens it, it loads the same Pomona you use in Safari, straight from Railway.
So sign-in, the week's plan, the list, chat — all of it is the website,
unchanged. The app adds what a website can't: an App Store listing, a home
screen icon, a launch screen, and push notifications.

Because the app shows the live site, **a change merged to `main` reaches the
app too, with no new App Store release.** A new release is only needed when
something in this folder changes (the icon, the permissions, a new plugin).

The website keeps working exactly as before for anyone using it in a browser
or from their home screen.

## How it's built

It uses Capacitor 8 (the tool that wraps a website in a real iOS app).

| File | What it holds |
|---|---|
| `capacitor.config.json` | The app's settings. `server.url` is **the one place the Pomona address is written.** Also the app id (`com.heypomona.app`), the user-agent tag and the launch screen timing. |
| `package.json` | The app version (`1.0.0`) and the exact Capacitor versions. |
| `www/offline.html` | The screen shown when the phone has no signal and Pomona can't open. |
| `www/index.html` | Required by Capacitor; never normally seen. |
| `ios/` | The Xcode project Capacitor generated. Icons, launch screen and iPhone permissions live in `ios/App/App/`. |
| `scripts/prepare-www.mjs` | Keeps the settings above in step and stops with a plain message if they disagree. |
| `scripts/make-assets.mjs` | Draws the app icon and launch screen from the Pomona mark. |

## How a build happens

Emily's Mac has no Xcode, so builds happen in the cloud, on Codemagic
(`codemagic.yaml`; see "Day one with the Apple account" below). The
builder runs, from this folder:

```
npm ci
npm run sync        # checks the settings, copies www/ into the iOS project
# then builds ios/App/App.xcodeproj (scheme "App") and uploads to TestFlight
```

`npm run sync` must run before every build: the iOS project's copy of the
settings and of `www/` is generated, not stored in git.

The cloud builder is **Codemagic**, set up by `codemagic.yaml` at the top
of the repo. It is started by hand, so nothing builds until you ask.

## Day one with the Apple account

Emily's step-by-step is `docs/TESTFLIGHT_WALKTHROUGH.md` (two steps at a
time, no code), and the phone checks before Beta App Review are
`docs/IPHONE_TEST_CHECKLIST.md`. In short, for whoever maintains this:

- App ID `com.heypomona.app` registered with **Push Notifications**; the app
  record made in App Store Connect.
- An App Store Connect API key (App Manager) added in Codemagic as the
  integration **Pomona App Store Connect** — the name `codemagic.yaml` uses.
- Signing is Codemagic's "Code signing identities" route, which does NOT
  make anything on its own: an **Apple Distribution** certificate generated
  in Codemagic (downloaded once and uploaded back), and an **App Store**
  profile made on the Apple developer site, then fetched into Codemagic.
  `ios_signing` in `codemagic.yaml` picks both up by bundle id.
- The workflow uploads for **internal** testing only
  (`submit_to_testflight: false`); Emily submits to Beta App Review by hand
  after the phone checklist. Once the public link is live, later builds can
  go straight to it: set `submit_to_testflight: true` and add
  `beta_groups: [Public testers]`.
- Build numbers: Codemagic's `$BUILD_NUMBER`, raised to one above Apple's
  newest build when `APP_STORE_APPLE_ID` (the app's number in App Store
  Connect > App Information) is set in the workflow's `vars`.
- Xcode 26 or newer is required by Apple since 2026-04-28 and by
  Capacitor 8; the workflow checks before it builds.

## Things worth knowing

- **Push notifications:** the app asks once, after a week is approved (never
  on first launch), and Preferences has an On/Off row. The server sends
  through Apple with a key that lives only in Railway: `APNS_KEY_ID`,
  `APNS_TEAM_ID`, `APNS_KEY_P8` (the .p8 file's text), `APNS_TOPIC`
  (`com.heypomona.app`), and `APNS_SANDBOX=1` only while testing a build run
  from Xcode. Until those are set, the morning note and the dinner nudge go
  by text, as before. The Push Notifications capability is in
  `ios/App/App/App.entitlements`; `npm run sync` checks it's switched on.

- **Privacy manifest** (`ios/App/App/PrivacyInfo.xcprivacy`): Apple's
  required list of what the app collects, whether it tracks anyone (no),
  and which "required reason" phone APIs its native code calls (none, as of
  Capacitor 8.5.2 and the three plugins). Apple refuses an upload without
  it, and the App Store privacy label has to agree with it. Update it when
  the server starts keeping a new kind of personal data (and change the
  privacy page and the label the same day), or when a plugin is added or
  upgraded: `npm run sync` scans the plugins and stops, naming the file,
  if one calls an API the manifest doesn't declare.
- **Changing the address** (a custom domain later): change `server.url` in
  `capacitor.config.json` **and** the first entry under `WKAppBoundDomains`
  in `ios/App/App/Info.plist`. `npm run sync` refuses to run if they disagree.
- **Changing the version:** change `version` in `package.json`, the
  `PomonaApp/…` tag in `capacitor.config.json`, and `MARKETING_VERSION` in
  the Xcode project. `npm run sync` checks all three. The build number
  (`CURRENT_PROJECT_VERSION`) goes up by one on every upload; the cloud
  builder does that.
- **The server can tell the app apart:** every request from the app carries
  `PomonaApp/<version>` at the end of its User-Agent. Error reports already
  show it as "iPhone · Pomona app".
- **Offline:** the site's service worker runs inside the app (that is what
  `WKAppBoundDomains` and `limitsNavigationsToAppBoundDomains` are for), so
  once Pomona has opened with signal, the shopping list still opens in a
  store with none. The bundled offline screen only shows when there's no
  saved copy yet (or, with signal, "Pomona didn't open" when the server
  doesn't answer).
- **Status bar:** the page starts below the iPhone's status bar, on a
  spruce strip with light text (`StatusBar` in `capacitor.config.json`), the
  same way the home-screen version starts below it. So the website needed
  no changes for the notch or Dynamic Island.
- **Links to other sites** open in Safari, not inside the app.
- **App id** `com.heypomona.app`, Emily's pick on 2026-10-25 to match the
  heypomona domain she owns (it replaced the `com.pomona.app` placeholder).
  It's set in `capacitor.config.json`, the Xcode project
  (`PRODUCT_BUNDLE_IDENTIFIER`), `codemagic.yaml` and `APNS_TOPIC`.
- **Icons:** `npm run assets` redraws them. Commit the PNGs it writes.
