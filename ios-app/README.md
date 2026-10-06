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
| `capacitor.config.json` | The app's settings. `server.url` is **the one place the Pomona address is written.** Also the app id (`com.pomona.app`), the user-agent tag and the launch screen timing. |
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

Everything below is signing in and pasting; nothing needs Xcode or a Mac.
Two steps at a time, so you can stop between any pair.

**1 and 2 — Apple.**
1. Join the Apple Developer Program at developer.apple.com/programs
   (US$99 a year; Apple can take a day or two to approve it).
2. In developer.apple.com > Certificates, Identifiers & Profiles >
   Identifiers, add an App ID: "Pomona", Bundle ID **explicit**
   `com.pomona.app`, and tick **Push Notifications**. (If Apple says the id
   is taken, pick another, e.g. `com.yourname.pomona`, and tell Claude: it
   is written in `capacitor.config.json`, the Xcode project and
   `codemagic.yaml`.)

**3 and 4 — the app's page and the key.**
3. In appstoreconnect.apple.com > Apps > **+** > New App: iOS, name
   "Pomona", language English, Bundle ID `com.pomona.app`, SKU `pomona`.
4. In App Store Connect > Users and Access > Integrations > App Store
   Connect API, make a **Team key** with the **App Manager** role. Download
   the `.p8` file (Apple lets you download it once) and copy the **Issuer
   ID** and **Key ID** shown on that page.

**5 and 6 — Codemagic.**
5. Sign in at codemagic.io with GitHub and add this repository.
6. In Codemagic > Team settings > Integrations > Developer Portal >
   Connect: paste the Issuer ID and Key ID, upload the `.p8`, and name it
   exactly **Pomona App Store Connect** (that name is what
   `codemagic.yaml` looks for).

**7 and 8 — the first build and testers.**
7. In Codemagic, open the app, choose the workflow "iPhone app to
   TestFlight", branch `main`, and Start new build. About 15-25 minutes.
   Codemagic makes the signing certificate itself from the key.
8. When it's green, App Store Connect > TestFlight shows the build
   (Apple takes a few more minutes to "process" it). Add yourself under
   Internal Testing, install the TestFlight app on your iPhone, and open
   Pomona from it.

**9 — push notifications.** In developer.apple.com > Keys, make a key with
**Apple Push Notifications service (APNs)** ticked, download its `.p8`,
and in Railway set `APNS_KEY_ID`, `APNS_TEAM_ID` (your Team ID, top right
of the developer site), `APNS_KEY_P8` (the whole text of the file) and
`APNS_TOPIC` = `com.pomona.app`. Until then the morning note goes by text.

Keys and `.p8` files go into Codemagic and Railway only, never into the
repo or a chat.

## Things worth knowing

- **Push notifications:** the app asks once, after a week is approved (never
  on first launch), and Preferences has an On/Off row. The server sends
  through Apple with a key that lives only in Railway: `APNS_KEY_ID`,
  `APNS_TEAM_ID`, `APNS_KEY_P8` (the .p8 file's text), `APNS_TOPIC`
  (`com.pomona.app`), and `APNS_SANDBOX=1` only while testing a build run
  from Xcode. Until those are set, the morning note and the dinner nudge go
  by text, as before. The Push Notifications capability is in
  `ios/App/App/App.entitlements`; `npm run sync` checks it's switched on.

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
- **App id** `com.pomona.app` is a placeholder until the Apple Developer
  account exists; Emily confirms it then. It's set in `capacitor.config.json`
  and in the Xcode project (`PRODUCT_BUNDLE_IDENTIFIER`).
- **Icons:** `npm run assets` redraws them. Commit the PNGs it writes.
