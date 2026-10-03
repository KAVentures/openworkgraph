# OpenWorkGraph — testing guide for new testers

This is the recommended way to try OpenWorkGraph and check that it works. It follows the dashboard as it is today. The version you are running is shown next to the name in the dashboard's top bar.

Plan on about 30 minutes for the first pass (steps 1–6) and a normal working day for the rest.

## 1. Install and start

### macOS

1. Download **OpenWorkGraph-macOS.zip** from the [latest release](https://github.com/KAVentures/openworkgraph/releases/latest) and unzip it.
2. Double-click **`START_OPENWORKGRAPH.command`**. This tester build is not yet signed by Apple, so macOS blocks it the first time:
   - **macOS 15 (Sequoia) and later:**
     1. Click **Done**.
     2. Open **System Settings → Privacy & Security** and scroll down to the message about `START_OPENWORKGRAPH.command`.
     3. Click **Open Anyway**, then confirm with **Open Anyway** and your password or Touch ID.
   - **macOS 14 and earlier:** right-click the file → **Open**, then **Open** again.
3. You only do this once. A Terminal window shows the first-time setup: OpenWorkGraph downloads its own private runtime, so you don't need Python.
4. Approve **Accessibility** and **Input Monitoring** when asked (System Settings → Privacy & Security). If a permission is missing, the recording pill in the dashboard says so.
5. The dashboard opens in your browser at `http://127.0.0.1:8787`.

### Windows

1. Download **OpenWorkGraph-Windows.zip** from the [latest release](https://github.com/KAVentures/openworkgraph/releases/latest) and unzip it.
2. Double-click **`START_OPENWORKGRAPH.cmd`**.
3. This tester build is not yet signed, so SmartScreen may say "Windows protected your PC". Click **More info → Run anyway**, but only for a file you downloaded from the OpenWorkGraph Releases page.
4. The first start downloads a private runtime (no Python needed). The dashboard then opens at `http://127.0.0.1:8787`.

**Expected:** the dashboard opens in the **Basic** view with four tabs: **Today, Activity, AI apps, Privacy**. Nothing needs an account. **Agents** appears once a coding agent is observed, and **Organization** once this computer joins one. The **Basic / Advanced** switch in the top bar shows every tab (History, Export, Settings and the rest); try it, then switch back. Your choice is remembered on this computer.

## 2. Add or update the browser sensor (recommended)

Without it, OpenWorkGraph sees "Google Chrome". With it, it can tell Gmail from Google Sheets from Salesforce and can provide richer browser-semantic evidence.

1. Run **`ADD_BROWSER_SENSOR.command`** on macOS or **`ADD_BROWSER_SENSOR.cmd`** on Windows. It opens a folder and your browser's extensions page.
2. Turn on **Developer mode**, click **Load unpacked**, and choose the folder that opened.
3. In the dashboard, the "Browser sensor not connected" chip should disappear and step 2 of the setup checklist on **Today** ticks itself off. If it doesn't, use **How → Pair / repair sensor** in that checklist (or **Advanced → Settings**).

**After upgrading OpenWorkGraph:** the unpacked browser extension may still be running the previous files. If the server observes an older sensor, **Advanced → Settings → Capture & privacy** shows **Browser sensor update available** with the running and expected versions. Open the browser's extensions page and click **Reload** for the OpenWorkGraph extension. Ordinary capture can continue while it is stale, but new browser-context features should not be considered active until the version warning disappears.

v0.114 uses browser sensor **1.14.0** for the new business-object reference feature.

## 3. Work normally, then look at Today

Work for 10–15 minutes as you normally would: email, a spreadsheet, a CRM, documents.

**Expected on Today:**

- **One status line** at the top, such as "11 min of work recorded so far · 5 tools · AI apps can't read it · nothing shared", with **See what happened**: a reconstruction of what you just did, with friendly names (Gmail, Google Sheets) rather than web addresses.
- **Get set up:** three steps (how long to keep your history, browser sensor, connect an AI app). Each ticks itself off when done; the list disappears when all are done, or use **Hide**. Until you choose, new history is kept for 7 days.
- **Today's timeline:** separate lanes per tool, with time that looks plausible. It is visible from the start (it says "Your timeline starts here" until the first minute of activity), and it updates when you come back to Overview.
- **Time by tool** and **Repeated workflows** below it. Empty sections explain what will appear instead of disappearing. The **Work profile** and **Workflow discovery** are in the Advanced view.

## 4. Check what is stored (privacy)

Open **Activity** and look at recent rows. The **Page or window** column shows the title. (In the Advanced view, **Navigation loops** and **Transitions** switch the view, and **Events** brings the table back.)

**Expected:**

- **Only your own work:** coding-agent runs are on the **Agents** tab, not in Activity.

- **Context is kept:** titles such as `Re: Contract renewal Q4 - Gmail`, `Q4 pipeline - Customer tracker - Google Sheets`, `Acme Logistics AB | Account | Salesforce`.
- **Personal details are tokens:** detected names, email addresses, phone numbers and personal identity numbers appear as tokens such as `PERSON_1A2B3C`, `EMAIL_…`, `PHONE_…`, `PERSONNUMMER_…`. The same person always gets the same token.
- **Typed text is never shown**, only counts.

Safe tests you can do (use fake data only):

| Put this in a document title or email subject | Expected in Activity |
|---|---|
| `Meeting with Anna Svensson about Q3` | `Meeting with PERSON_… about Q3` |
| `Call +46 70 123 45 67` | `Call PHONE_…` |
| `Card 4111 1111 1111 1111` (a standard test number) | `Card PAYMENT_CARD_…` |
| `DATABASE_PASSWORD=fake-test-password-123` | the value replaced with `SECRET_…` |
| `Sprint board: In Progress column` | unchanged (not a person) |

Never use real card numbers, passwords or other people's personal numbers for testing.

### v0.114 browser-context privacy profiles

**Privacy → Browser detail** offers **Standard** (Privacy-first) and **More context** (Context). The full set is in **Advanced → Settings → Capture & privacy**. The browser-context profile is independent of AI context detail and organization sharing.

- **Privacy-first (default):** business-object references are off. Known Google Docs/Drive, GitHub, Salesforce, Jira and Linear object-ID positions are masked in stored browser paths. This is slightly stricter than pre-v0.114 path behavior.
- **Context:** allowlisted work objects can be correlated across repeated events, but the provider's actual record/thread/document locator is not stored. The browser retry queue uses a pairing-keyed opaque fingerprint, and the local server replaces it with a separate installation-keyed `owg:r:…` token before event persistence.
- **Rich enterprise:** explicitly keeps the minimal validated provider locator as well, so an authorized connector/AI can resolve the object. It does **not** turn on file-upload categories or any other unrelated optional sensor.
- **Custom:** lets you set the same switches individually.

Supported reference shapes in v0.114 are Google Docs/Sheets/Slides/Drive, known Gmail conversation routes, GitHub PR/issues, Salesforce records, Jira issues and Linear issues. Unknown sites are not guessed.

Use fake objects for testing. Useful checks:

1. In **Privacy-first**, open a fake/test GitHub PR such as `/owner/repo/pull/123`. Evidence may preserve the useful route structure, but the object position should appear as `:id`; there should be no `resource_reference` metadata.
2. In **Context**, revisit the same supported object several times. The persisted `resource_ref` should be stable for that installation, begin with `owg:r:`, and should not contain the real PR/issue/record ID or a provider locator.
3. In **Rich enterprise**, repeat the test with a fake/test object. The validated minimal locator may be present in `resource_reference`, but the full URL, query string and fragment must still be absent.
4. Add a browser-host/title exclusion and verify that no rich locator from that excluded page is retained.
5. A Gmail `#search/<query>` route should **not** be treated as a conversation locator.

Do not use real customer, patient, employee, legal, financial or other sensitive records for these tests.

## 5. Privacy tab

**Expected:** one page with every everyday choice:

- **Recording:** **Pause**, work for a minute, then **Resume**. Nothing from the paused minute appears, even later. (The top bar has Pause and Stop too; **Stop** keeps your data, **Start new run** begins fresh.)
- **Keep my history:** one choice for everything kept. Pick **This session only** and confirm: the small run summaries are turned off too, so nothing is kept after the session.
- **AI apps can read my work:** off on a new install. Turn it on, quit and restart OpenWorkGraph: it is still on (your choice is remembered). In the Advanced view, **Turn AI access off every time OpenWorkGraph starts** brings back the old behavior.
- **Include older history:** lets AI apps read saved history for 24 hours, then switches itself off.
- **Hide names and contact details from AI apps:** on by default. Leave it on.
- **Never record:** add an app you have open (for example **Notes**) and keep working in it for a minute. **Expected:** it shows up only as "Excluded" in Activity, without its title, right away (no restart). Add a website (for example a test site) and the same holds for it with the browser sensor. Remove the entries again, or use **Restore the default list**.
- **Delete recorded activity:** try **Last 15 minutes** and confirm. It disappears from Activity.

## 6. Connect your AI (AI apps tab)

**Expected:** at the top, one **AI access** switch and a line saying what AI apps can read right now (your recent work, and older history). Below it, apps found on this computer are listed first under **Recommended**, and the rest are folded under "Other apps". Each app has up to three switches:

- **Context:** your AI can read the work context you allow. Try it: turn on Context for Claude Desktop or Claude Code, restart that app, and ask it *"What have I been working on today?"*.
- **Observe** (coding agents): OpenWorkGraph records how the agent works (steps, tests, files changed, tokens), never your prompts or its answers.
- **Brief** (Claude Code): new sessions start with a short summary of how past runs in the same project went.

The card under each app says what to do if something isn't arriving (for example "Start a new Claude Code session").

**Recent AI activity** lists every read an AI app made since OpenWorkGraph started. (In the Advanced view, **AI context detail** is **Redacted** by default; leave it there.)

## 7. Agents tab (if you use a coding agent)

With **Observe** on, run a coding agent (Claude Code, Codex, Cursor) for a few tasks.

**Expected:** each run shows what it did (commands, tests passing or failing, files edited) and, when Human capture is on, what you did during and after it. Under **Agent learning** you'll find:

- **Session briefs:** "Preview brief" shows exactly what an agent would receive. "Measure whether briefs help" holds the brief back from a random 1 in 5 sessions and compares outcomes. It says "not enough data yet" until each group has 10 sessions.
- **Did agent work hold up?** (off by default): checks whether pull requests your agents opened were merged and whether CI passed, using your own `gh` login.
- **Playbooks:** export a repeated workflow as a small file someone else (or their AI) can use, or import one. Like the runs list, it hides agents whose Observe is off unless you tick "Show past runs from agents whose Observe is off".

## 8. History (Advanced view)

**Expected:** your retention choices for human and agent work, **Run memory** (a small content-free summary of each run, kept 90 days; the switch saves immediately and asks before turning off, because that deletes it), **AI access to saved history** (off by default), and a list of saved sessions you can export or delete.

## 8b. Settings (Advanced view)

**Expected:** **Capture & privacy** (privacy profile and optional browser signals), **Browser sensor** (pair or repair), and **People OpenWorkGraph has learned** (reset learned names; your history is not deleted). **Organization** only covers joining and sharing with an organization.

Delete one session. **Expected:** it disappears and does not come back.

## 9. Export

From **Activity**, click **Export…** and download the **XLSX** or **CSV ZIP**. Summaries are exported by default; tick **Include every individual event** for a larger, detailed file.

**Expected:**

- **Redacted** is on by default. The file keeps titles, tools, timing and repeated workflows, with detected names, email addresses, phone numbers and personal numbers replaced by tokens. Detection is best effort: if you spot a real name, please report it.
- Business context such as customer, company and project names stays in the file. Review it before sharing outside your organization.

A useful test: upload the file to an AI assistant and ask:

> Read the data dictionary first. What repeated work, bottlenecks, handoffs or manual effort do you see? What internal tool or automation might help? For every recommendation, show the observed evidence and distinguish observations from inference. Do not infer typed text that was never captured.

## What is not a failure

- Some desktop apps expose few button or menu labels; missing labels there are normal.
- Engagement time is an estimate, not proof of continuous work.
- Name detection uses a name list and context. An unusual or all-lowercase name can occasionally be missed, and a company that is also a surname can be tokenized. Please report either case.
- After updating OpenWorkGraph, the browser extension may need a reload (the dashboard tells you when it observes a version mismatch).
- Deleting local evidence does not recall anything an organization Gateway already received.

## Reporting problems

Open an issue at <https://github.com/KAVentures/openworkgraph/issues> with:

- what you did;
- what you expected;
- what you saw;
- your OS and the version shown in the dashboard's top bar.

Don't paste evidence that contains real customer or personal data.