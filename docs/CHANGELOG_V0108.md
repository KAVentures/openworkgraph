# OpenWorkGraph (unreleased, planned v0.108): full context, no personal details

## Titles keep their context; only personal details become tokens
- **What changed:** before v0.108, browser tab titles were cut to the site name before storage. `Re: Contract renewal Q4 - Anna Svensson - Gmail` became `Gmail`. Desktop-app titles, meanwhile, were stored with names in them.
- **Now:** every title (browser and desktop alike), control label and URL path keeps its text. Only these become stable tokens, before anything is stored:
  - people's names;
  - email addresses and phone numbers;
  - personal identity numbers, IBANs, payment cards, credentials and labelled personal IDs.
- **Example:** `Re: Contract renewal Q4 - PERSON_x <EMAIL_y> - Gmail`.
- **Kept:** subjects, documents, projects, company and product names, order and invoice numbers, dates, amounts.
- **Same person, same token:** a name seen next to an email address gets the email-linked token everywhere.
- **Grouping:** browser events keep the recognised work surface (`page.surface`).
- **Upgrade:** evidence stored earlier is protected once on the first start, recorded as a privacy migration. Browser titles that older versions already cut down cannot be restored.
- **Fix:** an IBAN written in groups and followed by a word ("SE45 5000 … 7466 payment") was not recognised. It is now.
- **AI context "Full"** now means "titles as stored", which no longer contain names or contact details.

## Export is redacted by default
- **Defaults:** Export now defaults to **Redacted** and to including every captured event. Since v0.108 the difference from before is small: redaction runs a second pass that also covers anything captured before this version. History's JSON export is redacted too.
- **Honest text:** the export text now says what the file keeps (full titles and business context) and what it never contains.

## Easier first use
- **Value first:** the "See what OpenWorkGraph understands" reconstruction comes first, and the retention choice ("Keep this beyond today?") sits directly under it. Its text reflects whether history is currently kept or deleted.
- **Connect:**
  - lists apps found on this computer first, with a one-line recommendation;
  - folds the rest under "Other apps";
  - gains a **Brief** column for session briefs, next to Context and Observe.
- **Agents tab:** has a new **Agent learning** section with session briefs (preview and measurement), outcome tracking and playbooks. **History** keeps only retention, run memory, saved-history AI access and saved sessions.
- **Friendly labels:** the first-value reconstruction uses the same surface names as the rest of the dashboard (Gmail, not mail.google).
- **Branding:** the macOS launcher and console say OpenWorkGraph instead of Workflow Observer. Install folders are unchanged, so upgrades keep working.
- **Install steps:** README, README_FIRST and the testing guide give accurate macOS 15+ Gatekeeper ("Open Anyway" in Privacy & Security) and Windows SmartScreen ("More info → Run anyway") steps.
- **Testing guide:** `NONTECHNICAL_TESTING.md` is rewritten for the current dashboard (it still described v0.57).
