# v15 nontechnical test

1. Stop the previous Workflow Observer process with **Ctrl+C**.
2. Unzip the v15 standalone build.
3. Right-click `START_ON_MAC_STANDALONE.command` and choose **Open**.
4. Reload the browser sensor extension if you already installed an earlier version.
5. Work normally.

## What to verify

- Sitting still does not create extra focus events.
- Switching apps/tabs creates completed focus periods.
- Effort stays separated by work surface (Gmail, Google Docs, Lovable, GitHub, ChatGPT, etc.), not collapsed into Chrome.
- Keypress totals rise while typing, but no typed text appears anywhere.
- Browser clicks/focus/submits show as semantic events when the extension supports the page.
- After a sequence of activity, **Candidate task executions** appears in the dashboard.
- A candidate task shows surfaces, elapsed/engaged time, keys/clicks and a suggested label.
- The label is clearly marked low/medium confidence; it should not pretend to know intent.
- Repeating roughly the same completed task twice should eventually create a **Repeated task pattern**.

### Simple task test

On GitHub, quickly do something like:

`GitHub → Your repositories → New repository → focus repository-name field → click Create repository`

Workflow Observer should retain the underlying semantic actions and may infer a candidate such as `GitHub — Create repository` with the observed effort attached.

The task grouping is still heuristic. Incorrect boundaries or labels are useful findings for the next iteration.
