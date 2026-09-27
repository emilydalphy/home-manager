# Pomona for iPhone

## What this is

The iPhone app is a thin shell around the live Pomona website. When someone
opens it, it loads the same Pomona you use in Safari, straight from Railway.
So sign-in, the week's plan, the list, chat — all of it is the website,
unchanged. The app adds what a website can't: an App Store listing, a home
screen icon, a launch screen, and (later) push notifications.

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

Emily's Mac has no Xcode, so builds happen in the cloud (see the Loop Board
card "App Store: build + TestFlight"). The builder runs, from this folder:

```
npm ci
npm run sync        # checks the settings, copies www/ into the iOS project
# then builds ios/App/App.xcodeproj (scheme "App") and uploads to TestFlight
```

`npm run sync` must run before every build: the iOS project's copy of the
settings and of `www/` is generated, not stored in git.

## Things worth knowing

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
