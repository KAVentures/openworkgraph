# OpenWorkGraph v0.118.0

## Basic and Advanced views, one Privacy tab

The dashboard had grown to eight tabs, about 110 controls and 2,600 words on a new install, with privacy choices spread over seven tabs. v0.118 opens in a **Basic** view with four tabs and puts every everyday privacy choice in one place. Nothing was removed: the **Advanced** switch in the top bar (remembered on this computer) shows every tab and setting as before.

### Basic view

- **Today:** one status line ("11 min of work recorded so far · 5 tools · AI apps can't read it · nothing shared"), **See what happened**, and a three-step setup checklist (how long to keep history, browser sensor, connect an AI app) that disappears when done. Then the timeline, time by tool and repeated workflows. Work profile, Workflow discovery, idle time and key presses are in Advanced.
- **Activity:** the list of what was recorded, search and delete, with **Export…** at the top.
- **AI apps:** the apps, their switches and the read log. Scripting help and redaction lists are in Advanced.
- **Privacy:** see below.
- **Agents** and **Organization** appear in Basic as soon as they are in use (a coding agent is observed, or this computer joins an organization).

### Privacy tab

One page for everything a person usually wants to control:

- **Recording:** pause or resume.
- **Keep my history:** this session only, 7, 30 or 90 days, 1 year, or until you delete it. One choice now covers everything kept, including the small run summaries, so "This session only" really keeps nothing after the session.
- **AI apps can read my work**, **Include older history** (24 hours, always switches itself off) and **Hide names and contact details from AI apps**.
- **Browser detail:** Standard or More context.
- **Never record:** apps, websites and words in a window title. These lists existed before only in a config file; now they can be seen and edited, they apply to new activity immediately (the server applies them on arrival, so the recorder does not need a restart), and the default list (password managers; titles with password, private, incognito or bank) can be restored.
- **Delete recorded activity:** last 15 minutes, last hour, today or everything, each with a confirmation.

### AI access is remembered

The AI access switch used to turn itself off at every restart while each app's Context switch stayed on, so connected apps stopped working after a restart without an obvious reason. It is still **off on a new install**, every MCP call is still checked live, and the read log is unchanged; now your choice is remembered after a restart. **Turn AI access off every time OpenWorkGraph starts** (Privacy, Advanced view) restores the previous behavior. The state is stored with owner-only permissions; if the file cannot be read, access stays off.

### Fixes

- A dashboard tab kept open across a restart now says so and explains how to reopen it, instead of showing "unavailable" everywhere.
- **Export** includes summaries by default; every individual event is an explicit choice.
- **What would be shared?** no longer shows a "Window titles: Yes" table when no organization is connected; it says nothing is shared.
- The first-run "Getting started" and "How long should OpenWorkGraph keep this?" cards are replaced by the Today status line and checklist in both views; the reconstruction is still one click away.

### Look and feel

A calmer visual layer: segmented tabs, softer cards and shadows, one type scale, a recording indicator, switches and chips that match, and layouts that hold up at phone width.
