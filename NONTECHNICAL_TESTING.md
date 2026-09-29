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

**Expected:** the dashboard opens with a dark top bar showing **Recording**, and tabs **Overview, Evidence, Agents, Connect, Organization, History, Export**. Nothing needs an account.

## 2. Add the browser sensor (recommended)

Without it, OpenWorkGraph sees "Google Chrome". With it, it can tell Gmail from Google Sheets from Salesforce.

1. Run **`ADD_BROWSER_SENSOR.command`** on macOS or **`ADD_BROWSER_SENSOR.cmd`** on Windows. It opens a folder and your browser's extensions page.
2. Turn on **Developer mode**, click **Load unpacked**, and choose the folder that opened.
3. In the dashboard, the "Browser sensor" chip should turn green. If it doesn't, use **Pair / repair browser sensor**.

## 3. Work normally, then look at Overview

Work for 10–15 minutes as you normally would: email, a spreadsheet, a CRM, documents.

**Expected on Overview:**

- **See what OpenWorkGraph understands:** a first reconstruction of what you just did, with friendly names (Gmail, Google Sheets) rather than web addresses.
- **Keep this beyond today?:** directly under it. New installs start with "Don't keep after session". Choose **Keep 90 days** if you want OpenWorkGraph to learn patterns across days. You can change this at any time in **History**.
- Separate lanes per tool in the timeline, with time and effort that look plausible.

## 4. Check what is stored (privacy)

Open **Evidence** and look at recent rows.

**Expected:**

- **Context is kept:** titles such as `Re: Contract renewal Q4 - Gmail`, `Q4 pipeline - Customer tracker - Google Sheets`, `Acme Logistics AB | Account | Salesforce`.
- **Personal details are tokens:** people's names, email addresses, phone numbers and personal identity numbers appear as tokens such as `PERSON_1A2B3C`, `EMAIL_…`, `PHONE_…`, `PERSONNUMMER_…`. The same person always gets the same token.
- **Typed text is never shown**, only counts.

Safe tests you can do (use fake data only):

| Put this in a document title or email subject | Expected in Evidence |
|---|---|
| `Meeting with Anna Svensson about Q3` | `Meeting with PERSON_… about Q3` |
| `Call +46 70 123 45 67` | `Call PHONE_…` |
| `Card 4111 1111 1111 1111` (a standard test number) | `Card PAYMENT_CARD_…` |
| `DATABASE_PASSWORD=fake-test-password-123` | the value replaced with `SECRET_…` |
| `Sprint board: In Progress column` | unchanged (not a person) |

Never use real card numbers, passwords or other people's personal numbers for testing.

## 5. Recording controls

Use **Pause**, work for a minute, then **Resume**.

**Expected:** nothing from the paused minute appears, even later. **Stop** keeps the dashboard running and your data intact. **Start new run** begins fresh.

## 6. Connect your AI (Connect tab)

**Expected:** apps found on this computer are listed first under **Recommended**, and the rest are folded under "Other apps". Each app has up to three switches:

- **Context:** your AI can read the work context you allow. Try it: turn on Context for Claude Desktop or Claude Code, restart that app, and ask it *"What have I been working on today?"*.
- **Observe** (coding agents): OpenWorkGraph records how the agent works (steps, tests, files changed, tokens), never your prompts or its answers.
- **Brief** (Claude Code): new sessions start with a short summary of how past runs in the same project went.

The card under each app says what to do if something isn't arriving (for example "Start a new Claude Code session").

**AI context detail** (further down) is **Redacted** by default. Leave it there.

## 7. Agents tab (if you use a coding agent)

With **Observe** on, run a coding agent (Claude Code, Codex, Cursor) for a few tasks.

**Expected:** each run shows what it did (commands, tests passing or failing, files edited) and, when Human capture is on, what you did during and after it. Under **Agent learning** you'll find:

- **Session briefs:** "Preview brief" shows exactly what an agent would receive. "Measure whether briefs help" holds the brief back from a random 1 in 5 sessions and compares outcomes. It says "not enough data yet" until each group has 10 sessions.
- **Did agent work hold up?** (off by default): checks whether pull requests your agents opened were merged and whether CI passed, using your own `gh` login.
- **Playbooks:** export a repeated workflow as a small file someone else (or their AI) can use, or import one.

## 8. History

**Expected:** your retention choices for human and agent work, **Run memory** (a small content-free summary of each run, kept 90 days, which you can turn off or delete), **AI access to saved history** (off by default), and a list of saved sessions you can export or delete.

Delete one session. **Expected:** it disappears and does not come back.

## 9. Export

On **Export**, download the **XLSX** or **CSV ZIP**.

**Expected:**

- **Redacted** is on by default. The file keeps titles, tools, timing and repeated workflows, and contains no names, email addresses, phone numbers or personal numbers.
- Business context such as customer, company and project names stays in the file. Review it before sharing outside your organization.

A useful test: upload the file to an AI assistant and ask:

> Read the data dictionary first. What repeated work, bottlenecks, handoffs or manual effort do you see? What internal tool or automation might help? For every recommendation, show the observed evidence and distinguish observations from inference. Do not infer typed text that was never captured.

## What is not a failure

- Some desktop apps expose few button or menu labels; missing labels there are normal.
- Engagement time is an estimate, not proof of continuous work.
- Name detection uses a name list and context. An unusual or all-lowercase name can occasionally be missed, and a company that is also a surname can be tokenized. Please report either case.
- After updating OpenWorkGraph, the browser extension may need a reload (the dashboard tells you).
- Deleting local evidence does not recall anything an organization Gateway already received.

## Reporting problems

Open an issue at <https://github.com/KAVentures/openworkgraph/issues> with:

- what you did;
- what you expected;
- what you saw;
- your OS and the version shown in the dashboard's top bar.

Don't paste evidence that contains real customer or personal data.
