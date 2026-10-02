# OpenWorkGraph v0.117.0

## A clearer, calmer dashboard

v0.117 is a dashboard release. Capture, storage, MCP and the browser sensor are unchanged (browser sensor 1.14.0 stays current); what changes is what you see and where you find it.

### Fixed

- **Today's timeline shows again.** A first-run helper hid the timeline card before the timeline had loaded, and nothing ever showed it again. The card now stays visible with its own loading and empty states, and it refreshes when you switch back to Overview.
- **Evidence has a way back.** After choosing Navigation loops or Transitions there was no button to return to the event list. An **Events** button now sits next to them.
- **No stale version number.** The top bar briefly showed v0.56.1 or v0.57.0 before the real version loaded. It now shows nothing until the real version arrives.
- **One recording clock.** Two scripts used to overwrite the recording label in turn. The capture status now owns it, and the elapsed time ticks every second locally.
- Self-tag and capture-settings confirmations used a toast function that did not exist; they now show.

### Evidence keeps its context

The Evidence table used to show only the tool name in the Page column. It now shows the stored page or window title, the same context exports and redacted AI context already keep (for example "Re: Contract renewal Q4 - PERSON_1A2B3C - Gmail"). Detected names, email addresses, phone numbers and personal identity numbers are tokenized before storage, and the dashboard runs the same best-effort protection once more for display, including legacy rows. Display protection runs before title truncation so a cutoff cannot turn a sensitive identifier into an unrecognisable fragment. It fails closed: if the check cannot run, only the tool name is shown. URL paths and raw button labels are still never sent to the dashboard.

### Human views show human work

- **Evidence** lists your own work only. Agent runs (Claude Code, Codex, Cursor and others) are on the Agents tab.
- **Work profile** is computed from your own work only, so a coding agent no longer appears as "AI-tool usage" with zero minutes, and agent activity no longer feeds the transfer, repeated-page or friction signals.
- **Playbooks** follow the Agents tab's choice: workflows run only by agents whose Observe switch is off are hidden unless you tick "Show past runs from agents whose Observe is off".

### One place for AI access

There used to be three: a switch in Connections, a second Enable button in an "AI access" card, and saved-history access in History. Now:

- **Connect** has the one switch, "AI access this run", with a plain explanation that it turns itself off every time OpenWorkGraph starts, and a summary line of everything AI apps can read (this run, and older saved history with a link to change it).
- The old second card is now **Recent AI activity**, a read-only log of what was read.
- **History** keeps the saved-history permission and points back to the summary in Connect.

### Settings tab

Personal capture settings moved out of **Organization** into a new **Settings** tab: Capture & privacy (privacy profile, browser signals), Browser sensor pairing, and learned names. Organization now only holds joining and sharing with an organization. The "Browser sensor not connected" chip opens Settings.

### Overview reads in order

Overview now shows what happened first (metrics, Today's timeline, Repeated workflows, Time by tool) and the Work profile after it, since the profile interprets those. Repeated workflows and Time by tool show an explanation when empty instead of disappearing.

### Plain language

- "Work surfaces" is now "Tools used"; "Effort by work surface" is "Time by tool".
- "Navigation / hunting candidates" is "Pages you kept going back to"; "Friction candidates" is "Possible friction" with "Repeated clicking" and "Sign-in steps"; "Tool waiting" is "Waiting for pages to load".
- Agent observation levels show as "Full trace", "Tool calls only", "App activity only" and so on, not internal codes.
- Run memory and Playbooks no longer show internal workflow keys.
- Notes that referred to unbuilt "stacked PRs" are gone.

### Controls

- Run memory, outcome tracking and brief measurement are switches that save immediately, with a confirmation when turning one off deletes data. No separate Save button.
- Delete buttons are quiet until you mean it: Evidence deletion moved below the table, and the final "Delete evidence" confirmation is the red action. Deleting an imported playbook now asks first.
- Selects, date pickers, file pickers, text areas, checkboxes and switches share one style across tabs.

### Performance and small screens

- The dashboard checks capture, AI-access, organization and timeline state every 5 seconds (it was every second), and only while the page is visible.
- On a phone, the tab bar fades at the edge to show it scrolls, the selected tab scrolls into view, and Work profile tiles use two columns.
