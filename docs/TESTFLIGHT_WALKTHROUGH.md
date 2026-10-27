# Getting Pomona onto TestFlight

For Emily. Everything here is signing in, clicking and pasting. No Xcode, no
Mac, no code. It's in pairs, so you can stop after any pair and pick up later.

**Before you start:** your Apple Developer account under Meteor Strategy is
approved, and you can sign in at developer.apple.com and
appstoreconnect.apple.com. Have a notes file open on your computer for three
things you'll copy along the way. Keep it private. Keys never go in an email,
a chat or the repo.

Words in **bold** are what's on the screen, so you can look for them.

If App Store Connect shows a banner asking you to review an agreement,
accept it. You can leave Paid Apps, tax and banking alone: a free beta
doesn't need them.

The build settings only appear in Codemagic once the branch with this
walkthrough is merged to `main`. I'll tell you when it is.

---

## 1 and 2: Tell Apple about the app

**1. Register the app's ID.**
Go to developer.apple.com → **Account** → **Certificates, IDs & Profiles** →
**Identifiers** → the blue **+**.
Pick **App IDs**, then **App**, then **Continue**.
- Description: `Pomona`
- Bundle ID: **Explicit**, and type `com.heypomona.app`
- Scroll the list of capabilities and tick **Push Notifications**.

Click **Continue**, then **Register**.

If Apple says the ID is taken, stop here and tell me. It's written into the
app in three places and I'll change them.

**2. Create the app in App Store Connect.**
Go to appstoreconnect.apple.com → **Apps** → the blue **+** → **New App**.
- Platforms: **iOS**
- Name: `Pomona: Family Meal Planner` (the store name. Under the icon on a
  phone it still says Pomona.) If Apple says the name is taken, try
  `Pomona Meal Planner` or `Pomona: Weekly Meal Planner`, and tell me
  which one stuck.
- Primary language: **English (U.S.)**
- Bundle ID: pick **com.heypomona.app** from the list
- SKU: `pomona`
- User access: **Full Access**

Click **Create**.

Then open **App Information** on the left and copy the **Apple ID** number
into your notes. Send me that number. It's not a secret. I'll add it to the
build settings so build numbers can never clash with one Apple already has.

---

## 3 and 4: Make the key, and sign up for Codemagic

**3. Make the key that lets Codemagic talk to Apple.**
In App Store Connect → **Users and Access** → **Integrations** →
**App Store Connect API**.

The first time, there's a **Request Access** button instead of a list of
keys. Click it, agree to the terms and submit. Apple can take a while to
say yes. If it isn't instant, stop here and come back when Apple's email
arrives.

Once you're in: **Team Keys** → the **+** (the very first time, the
button says **Generate API Key**).
- Name: `Codemagic`
- Access: **App Manager**

Click **Generate**. Then:
- Click **Download API Key**. Apple lets you download it **once**. Save the
  file (it ends in `.p8`) somewhere safe.
- Copy the **Issuer ID** (above the list) and the **Key ID** (in the key's
  row) into your notes.

**4. Make your Codemagic account and connect GitHub.**
Go to codemagic.io → **Sign up** → **Sign up with GitHub**. Let it see the
`emilydalphy/home-manager` repository when GitHub asks.
Then **Add application** → GitHub → `home-manager`. If it asks for a project
type, pick **Ionic Capacitor App** (or **Other** if that isn't listed).

Stay on your own account. Don't create a Team: Teams cost money, and the
free build minutes only come with a personal account.

---

## 5 and 6: Give Codemagic the key, then make the signing certificate

**5. Paste the key into Codemagic.**
In Codemagic, open your account's **Settings** (left side, under your
name) → **Integrations** → **Developer Portal** → **Connect** (or
**Manage keys** → **Add key**).
- Name: type exactly `Pomona App Store Connect`. The build looks for this
  name, letter for letter.
- Paste the **Issuer ID** and the **Key ID** from your notes.
- Upload the `.p8` file from step 3.

Click **Save**.

**6. Make the signing certificate.** This is Apple's proof that the app
really comes from you.
In Codemagic → your account's **Settings** → **codemagic.yaml settings** →
**Code signing identities** → **iOS certificates** → **Generate certificate**.
- Reference name: `Pomona distribution`
- Certificate type: **Apple Distribution**
- API key: **Pomona App Store Connect**

Click **Create certificate**. Codemagic shows a **Download** button and a
**password**. You only get one chance at both:
- Download the file (it ends in `.p12`) and copy the password into your notes.
- Then, on the same tab, upload that file back: **Choose a .p12 or .pem file**,
  paste the password, reference name `Pomona distribution`, **Add certificate**.

Yes, you download it and then upload it straight back. That's how Codemagic
keeps a copy it can use.

---

## 7 and 8: The profile, then the first build

**7. Make the App Store profile.** It ties the app's ID to the certificate.
Back on developer.apple.com → **Certificates, IDs & Profiles** → **Profiles**
→ the blue **+**.
- Under Distribution, pick **App Store Connect**. **Continue**.
- App ID: **com.heypomona.app**. **Continue**.
- Certificate: pick the one made today (it says Apple Distribution and
  today's date). **Continue**.
- Name: `Pomona App Store`. **Generate**.

You don't need to download it. Then in Codemagic → **Code signing identities**
→ **iOS provisioning profiles** → **Fetch profiles**. Tick
**Pomona App Store**, give it the reference name `Pomona App Store`, and
click **Download selected**. It should show a green tick under Certificate.

**8. Run the first build.**
In Codemagic, open the **home-manager** app → **Start new build**.
- Branch: `main`
- Workflow: **iPhone app to TestFlight**

Click **Start new build**. It takes 15 to 30 minutes. You'll get an email
either way.

If it goes red, send me the link to the build page. The first build of an
app often trips once or twice. That's normal, and fixing it is my job.

---

## 9 and 10: You and Vineeth try it

**9. Add yourselves as internal testers.** Internal testers don't need
Apple's review, so you can try it the same day.
- If Vineeth isn't on the account yet: App Store Connect → **Users and
  Access** → **+** → his name and email, role **Developer**. He gets an
  email and accepts it.
- When the build's email from Apple says it's done processing (usually
  10 to 30 minutes after Codemagic goes green): App Store Connect → **Apps** →
  Pomona → **TestFlight** → **Internal Testing** → the **+** → name the
  group `Us`. Add yourself and Vineeth, and add the build.

**10. Install it and run the phone checklist.**
On each iPhone, install **TestFlight** from the App Store, open the email
from Apple, and tap **View in TestFlight** → **Install**.
Then go through `docs/IPHONE_TEST_CHECKLIST.md`, one tick at a time. Tell
me anything that doesn't tick. Don't go on to step 11 until the list is
clean.

---

## 11 and 12: Send it to Apple's beta review

**11. Fill in the test information.**
App Store Connect → Pomona → **TestFlight** → **Test Information** on the
left. The words are already written for you in `docs/APP_STORE_LISTING.md`,
section 11. Paste them in:
- **Beta App Description**: the description.
- **Feedback Email**: your feedback address (hello@heypomona.com once it
  exists).
- **Privacy Policy URL**: the live site's address with `/privacy` on the
  end. Apple needs this before outside testers can join. The privacy page
  has to have your company name, address and contact email filled in first.
- **Beta App Review Information**: your name, phone and email.
  Tick **Sign-in required**. Username `demo`. Password: the demo
  passphrase. It's `DEMO_PASSPHRASE` in Railway's settings. Type it here
  only. Before you submit, ask me to check the demo household is set up
  on the live site, so Apple's reviewer can really get in.
- **Review Notes**: section 9 of the same file, word for word.

Click **Save**. Then, on the build, paste section 11's **What to Test**.

**12. Make the public group and submit.**
**TestFlight** → **External Testing** → the **+** → name it
`Public testers`. Add the build to it. Apple asks a couple of questions
before sending it:
- Encryption: the app already tells Apple it uses only standard web
  security, so this shouldn't come up. If it does, answer **None of the
  algorithms mentioned above**.

Click **Submit for Review**. Apple's beta review usually takes about a day,
and it's only this long the first time.

---

## 13: Turn on the public link

When Apple's email says the build is approved:
**TestFlight** → **External Testing** → **Public testers** →
**Public Link** → **Enable Public Link**. Set a tester limit if you want
one (up to 10,000). Copy the link. That's the link you share.

Each build lasts 90 days. I'll make a new one before then, and whenever
the app's icon, permissions or address change. Fixes to Pomona itself
reach testers with no new build at all.

---

## Later: notifications on the lock screen

Not needed for the first build. When you want push notifications:
developer.apple.com → **Keys** → **+** → name `Pomona push`, tick **Apple
Push Notifications service (APNs)** → **Continue** → **Register** →
**Download** (once only, a `.p8` file). Copy the **Key ID**, and your
**Team ID** from the top right of the page. Then we put four settings in
Railway together, in a 1–6am window. Until then, the morning note goes by
text.

---

## If something goes wrong

- **"No matching profiles found"** in the build: step 7 wasn't finished.
  Check the profile shows in Codemagic with a green tick.
- **The build doesn't show "iPhone app to TestFlight"**: the branch with
  these settings isn't merged to `main` yet.
- **"Cannot find integration Pomona App Store Connect"**: the key's name in
  step 5 doesn't match. Rename it there to exactly `Pomona App Store
  Connect`.
- **"You already have a current Distribution certificate"** in step 6:
  Apple allows only a few. Tell me, and we'll remove an old one together.
- Anything else: send me the build page link. Don't paste keys or
  passwords into the chat.
