# OpenWorkGraph Browser Sensor v1.15

Local structural browser capture for OpenWorkGraph.

The sensor records navigation, tab activation, pointer activation, focus on editable controls, form submissions, control changes, and copy/paste occurrence. It does **not** record typed values, clipboard contents, password contents, URL query strings, or fragments.

## Pairing

Normal pairing is now explicit but code-free:

1. Keep OpenWorkGraph running.
2. Open the extension and choose **Connect to OpenWorkGraph**.
3. The extension opens a localhost approval page.
4. Choose **Connect browser**.

The resulting credential is bound to the local OpenWorkGraph installation and stored only in browser extension storage. The older 8-digit dashboard pairing code remains a fallback.

Unpacked development builds can also receive a generated local `pairing.json` from the desktop package.

## Enterprise distribution

GitHub Releases build a store-ready ZIP of this directory. Publish that ZIP through the organization-owned Chrome Web Store / Edge Add-ons account, then use the policy templates in `enterprise/browser/` to force-install the real extension ID. Store publication itself requires external publisher credentials and review and is not faked by the open-source build.

## Browser-only pause

The popup provides **Pause browser recording** and **Resume browser recording**. This per-browser-profile setting survives extension/background restarts and does not change desktop recording or any sharing permissions. While paused the extension blocks structural events, optional page/draft text and enhanced interaction signals; buffered local evidence and unsent queued browser events are discarded. Already-delivered events are not deleted. A content-free `paused` heartbeat continues so the dashboard can distinguish a paused browser from a disconnected sensor. Existing OWG exclusions and separate capture-depth/work-text consent remain in force after resuming.

## Earlier Never record exclusion enforcement

The browser extension applies the dashboard's locally cached excluded host/domain and window-title lists **before** recording or queuing structural browser evidence. High-fidelity hover/drag/trajectory signals are suppressed before their expensive page probes. Excluded page details are also omitted from browser heartbeats. A changed exclusion list purges the matching unsent browser event backlog. The server continues to recheck exclusions independently at ingest, including when the extension has been offline; already-delivered records follow the existing deletion/retention process.

## Exclude a website from the extension popup

Open the browser sensor's popup while visiting an ordinary HTTP(S) website.
Choose **Never record this site**. This adds the active website's **hostname
only** to a separate browser-only excluded-host list. It is combined with,
never overwritten by, the OWG-managed domain/title exclusion policy refreshed
from the local server. It also excludes its
subdomains, but does not collect or save the URL path, query, page text,
or anything about the open page. Sites already covered by a parent-domain
or wildcard exclusion cannot be added twice.

Use **Remove** next to a browser-only domain to resume future eligible
capture on that domain **only if OWG's separate managed exclusions also allow it**. Up to 100 domains may be saved. The popup displays a clear
error if browser storage is unavailable and refuses to modify the policy
rather than assuming exclusions can be discarded.

The popup **never edits OWG-managed site or title exclusion patterns** and introduces no server calls, cloud permissions or capture-depth
changes. It changes future browser recording only, not desktop capture or
previously stored evidence; removing an exclusion does not bypass normal
pause, consent, privacy or work-text settings. The shared capture gate applies the **union** to structural events,
high-fidelity interaction signals, AI-agent lifecycle metadata, and separately
opted-in page/draft work-text capture. Rich text collection waits for both
policies to initialize, and its paired background delivery checks the local
exclusion again before sending over loopback. A newly excluded domain also
purges matching unsent structural evidence. Neither source's restrictions
can be silently cleared by refreshing the other source.

This is a convenient local
control, not a claim that earlier captured data has been deleted. Use OWG's
existing deletion controls separately if needed.
