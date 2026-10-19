# Everyone gets their own sign-in — the plan

Card: *"Mockups: everyone in the household has their own login, all connected to the
same household"* (High, Design checked — proposal only). Emily, 2026-10-18: *"make sure
each user has their own login info but they're connected to the same household. Walk
through what that process looks like and make sure we have clear mockups for it."* Then:
*"set up the process for real user logins to be created."*

The screens are in **`mockups/own-logins.html`** (open it in a browser; it has a
Light / Dark switch at the top). This file is the same thing in words, plus the build
plan and four questions.

**Supersedes the older Loop Board card "everyone has their own login".** That card and
this one are the same job; when this one is approved, close the old one as merged into
it. (Not done from here — the board can't be reached from this session.)

---

## Where we are today (checked in the code, 2026-10-19)

More of this is built than it sounds:

- **Sign in with email + a 6-digit code works**, for new households and for any adult
  who has added an email. It's **switched off on the live site** because there's no
  mail account (SMTP) set up on Railway yet, so no code can be sent.
- **An invite link works.** Emily taps "Invite Vineeth", a one-time link goes out by
  text, he opens it and he's in, as Vineeth. After that he's *offered* to add an email,
  with a "Not now".
- **Adults can add a sign-in email in Settings.** Changing an existing one needs a
  code sent to the old address too, so nobody can take over someone else's sign-in.
- **The passphrase still works for every household,** and after it, a two-adult house
  is asked "Who's this?". That pick is trusted, not proven — anyone holding the
  passphrase can pick anyone.
- **"Remove me"** exists (an adult leaving). Removing *someone else* doesn't.

So the job is mostly: make the email the way in for everyone, move today's households
over, and add the few pieces that are missing (People list, removing someone, signing
out a lost phone, a lighter level for a teen or helper).

---

## The flow, in plain words

**A. Emily signs up and starts the household.** She opens Pomona, types her email and
taps *Send me a code*. She types the 6 numbers from the email and taps *Come in*. Her
email is new to Pomona, so it makes a new, empty household and drops her into setup.
Setup's first question is *What's your name?* — "Emily" — and from then on that email
is Emily's. She adds Vineeth (adult) and Theo (7) as part of setup, like today.

**B. She invites Vineeth.** In Settings → People, Vineeth's row says "Not signed in
yet" and has an *Invite Vineeth* button. It shows her the message he'll get, and *Send
Vineeth's link* opens her phone's share sheet — Messages, Mail, WhatsApp, whatever she
uses. The link works once, within a week.

**C. Vineeth opens the link and signs up as himself.** The page says *Hi Vineeth.
Emily's added you to your household.* He types his own email, gets his own code, taps
*Join the household*, and lands on Today — the same week and the same list Emily sees.
No passphrase, no "Who's this?". His email now belongs to *him*, in *her* household.
(If he'd skipped the link and downloaded the app instead, his email would start an
empty household of its own. Setup's first screen will say *"Joining someone's
household? Ask them for the invite link."* and the abandoned one lets go of his email
when he joins — that part is built.)

**D. Vineeth gets a new phone.** He opens Pomona, types his email, types the code, and
he's himself, in his household. Settings → You shows his email and every phone he's
signed in on, with *Sign out* next to each, so a lost or sold phone can be shut out
from any other phone.

**E. Moving today's passphrase households over** (Emily's, Julia's, Gowthami's, Tester
Home). Nobody's household is copied, rebuilt or moved. Each adult already exists in
their household with all their history; we just attach an email to that person.
1. Once SMTP is on, each adult who signs in with the passphrase sees a one-time notice:
   *Your own sign-in — Instead of the shared passphrase, each of you signs in with your
   own email. Your plan, list and recipes stay just as they are.* → *Add my email*.
2. Settings → People shows who has moved over and who hasn't, with *Remind him* (sends
   the same one-time link as an invite).
3. When every adult in that house has an email, the passphrase stops opening it (see
   Question 2 for exactly when). Until then, both work.
4. Emily's own household goes last, after hers has been working by email for a while.
   Her passphrase is the one set on Railway, so turning it off is one setting, and
   turning it back on is the same setting.

**F. Removing a person.** Settings → People → tap the person → *Remove Maria from the
household* → *Remove Maria?* *She'll be signed out on her phones. Everything she added
stays with the household.* Her phones stop opening the household on their next tap;
her email gets a short note. Her name comes off things that pointed at her, but the
household's record stays (the week she approved stays approved). Leaving yourself is
today's "Remove me", renamed *Leave this household*.

**G. Lost email or lost phone.**
- *Lost phone:* sign in on the new one with email + code (step D), then sign the old
  one out from Settings → You.
- *Lost the email account* (changed jobs, closed an address): another adult in the
  household taps *Send a new link* on that person's row. Opening it lets them choose a
  new email — the link is the household vouching for them, standing in for the code to
  the old address. The old address still gets told it was changed.
- *Lost the email and nobody else is in the household:* they write to Pomona support
  and Emily sorts it by hand. That's rare enough not to build for yet.

**H. A teen or a helper who should see less.** Today only adults sign in, and every
adult sees everything. A helper (a nanny, a grandparent who cooks) can already be
invited as an adult who's "not eating here". Proposed: one lighter level, **Helper**,
chosen when inviting — they see Today, Shop and Cook, can tick things off and add to
the list, but can't approve the week, change settings, see People or delete anything.
A teen (13+) is invited the same way. Under 13 doesn't sign in. (Question 4.)

---

## What changes for the household

- **"Who's this?" goes away.** The sign-in *is* the person. Nobody is asked, and
  nobody can pick someone else.
- **"Approved by Vineeth" is true.** It names whoever signed in and tapped Approve —
  same for grocery adds, the pre-shop drops and the week's questions.
- **Notifications go to a person.** "Emily settled the week" reaches Vineeth's phones,
  and his dismissing it doesn't clear it for Emily (today one tap clears it for the
  whole house).
- **"Since you last looked" becomes possible.** Each sign-in remembers when that person
  last opened Pomona, so the app can tell Vineeth what Emily did since, and only what's
  new to him. (Not built today; this is what makes it buildable.)

---

## Build plan, in slices

Each slice is a night's work or less, and each can go live on its own.

**Slice 0 — Email that actually sends (Emily + one setting, no code).**
Pick a sending service, put its four settings (`SMTP_HOST`, `SMTP_USER`,
`SMTP_PASSWORD`, `SMTP_FROM`) on Railway, and set up the sender's domain so codes
don't land in spam. *Everything below depends on this.*
- Done when: from the live site, a code reaches Emily's inbox (not spam) in under a
  minute, on Gmail and on one other provider; "Something not working?" emails still
  arrive.

**Slice 1 — The login is a person.** The link between an email and a household member
already exists (`member_emails`). Add what's missing around it: a way to end a
person's sessions (a per-person counter the cookie carries; bumping it signs out their
phones), a "last used" record per phone, and a per-person "last opened" time.
- Done when: every adult who signs in by email or invite is that person on every
  request, with no "Who's this?"; bumping the counter signs out that person's other
  phones on their next tap and nobody else's; a test checks a removed person's old
  phone can't get back in.

**Slice 2 — Invite acceptance asks for their own email.** The join page asks for the
email first (no "Not now"), sends a code, and only spends the link once the code is
right. Settings → People gets *Invite* / *Invited · Send a new link* / the email on
each row (moving today's invite rows out of What we know). Setup's first screen gets
the "Joining someone's household?" line.
- Done when: on a fresh phone, Vineeth's link → email → code → Today as Vineeth, with
  his email on his member; closing the page before the code leaves the link working;
  a used, expired or replaced link says "This link has run out" as today; an email that
  already runs a household of its own is told so plainly and nothing moves.

**Slice 3 — Settings: You and People.** "You": your email (Change), the phones you're
signed in on with *Sign out* and *Sign out of every other phone*. "People": everyone,
how they get in, tap for their sheet; *Remove* for another person, *Leave this
household* for yourself; the removed person's email gets a note.
- Done when: Emily can remove Maria in two taps; Maria's phone is signed out on its
  next tap; the week Maria approved stays approved; nothing else in the household
  changes (row counts checked in a test); household 1 still can't be deleted from inside.

**Slice 4 — Moving the passphrase households over.** The one-time notice, *Remind
him*, and a per-household "passphrase off" switch that flips itself when the last adult
adds an email (after the overlap Emily picks in Question 2). The front door hides the
passphrase link for a household once its passphrase is off. Emily's env passphrase is
switched off last, by hand.
- Done when: rehearsed on a **copy** of the live database on a throwaway server (never
  the real one): every table's row count is identical before and after, apart from the
  new email rows and the switch; each of the four households can be signed into by
  email as each of its adults; switching the passphrase back on restores today's
  sign-in exactly.

**Slice 5 — Per-person, where it shows.** Notifications addressed to a person and
dismissed per person; "Approved by" read from the signed-in person only; the
"last opened" time ready for the "Since you last looked" card (the card itself is its
own ticket). Then "Who's this?" and its code are deleted.
- Done when: Emily approving puts the notice on Vineeth's phones only; his dismissing
  it leaves hers; no screen anywhere asks "Who's this?".

**Slice 6 — Lost email, and the Helper level.** *Send a new link* on a joined adult lets
them set a new email without the old-inbox code (old address told). The Helper level
from H, if Emily says yes to Question 4.
- Done when: a re-invited adult with a dead email ends up signing in with a new one and
  keeps all their history; a Helper can tick and add to the list but gets no Approve,
  no Settings beyond "You", no People, and the server refuses those actions too (not
  just hides the buttons).

---

## Open questions for Emily

1. **Does the invited person *have* to add an email when they join, or can they skip
   it?** *Recommended: they have to.* The link only works once, so someone who skips
   has no way back in on a new phone or after signing out — that's the "Who's this?"
   problem again, just later. It's one extra screen and one code.

2. **When does the passphrase stop working for a household?** *Recommended: as soon as
   every adult in that house has added an email, plus two weeks.* Both ways work during
   the two weeks, so nobody gets locked out mid-week. Emily's household goes last, by
   hand.

3. **Can one adult remove another adult, or only themselves?** *Recommended: any adult
   can remove anyone in the household,* with the confirm screen and an email to the
   person removed. Two-adult houses change (separations, a helper who's moved on), and
   "they have to remove themselves" leaves the other person stuck with no way out.

4. **Build the lighter "Helper" level for teens and helpers now, or later?**
   *Recommended: later, as the last slice.* Helpers can already be invited as adults
   "not eating here". Get everyone onto their own sign-in first; then add Helper (sees
   Today, Shop and Cook; can tick and add to the list; can't approve, change settings
   or see People), for 13 and up only.
