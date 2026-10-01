(function (root) {
  "use strict";

  const SAFE_ID = /^[A-Za-z0-9_-]{8,200}$/;
  const ISSUE_KEY = /^[A-Z][A-Z0-9]{1,20}-[1-9][0-9]{0,10}$/;
  const SALESFORCE_ID = /^[A-Za-z0-9]{15}(?:[A-Za-z0-9]{3})?$/;
  const GITHUB_PART = /^[A-Za-z0-9_.-]{1,100}$/;
  const WORKSPACE = /^[A-Za-z0-9_-]{1,100}$/;
  const GMAIL_DIRECT_THREAD_ROUTES = new Set([
    "inbox", "all", "sent", "drafts", "spam", "trash", "starred", "snoozed", "important"
  ]);

  function candidate(provider, resourceKind, resolverLocator, host) {
    return {
      provider,
      resource_kind: resourceKind,
      resolver_locator: resolverLocator,
      host: String(host || "").toLowerCase(),
      resolution: "observed",
    };
  }

  function parseGoogleWorkspace(u) {
    const parts = u.pathname.split("/").filter(Boolean);
    const kindByPrefix = {
      document: "document",
      spreadsheets: "spreadsheet",
      presentation: "presentation",
    };
    const prefix = parts[0];
    if (u.hostname === "docs.google.com" && kindByPrefix[prefix] && parts[1] === "d" && SAFE_ID.test(parts[2] || "")) {
      const kind = kindByPrefix[prefix];
      return candidate("google_drive", kind, `${kind}:${parts[2]}`, u.hostname);
    }
    if (u.hostname === "drive.google.com" && parts[0] === "file" && parts[1] === "d" && SAFE_ID.test(parts[2] || "")) {
      return candidate("google_drive", "file", `file:${parts[2]}`, u.hostname);
    }
    return null;
  }

  function parseGmail(u) {
    if (u.hostname !== "mail.google.com") return null;
    const fragment = String(u.hash || "").replace(/^#/, "");
    if (!fragment) return null;
    const parts = fragment.split("/").filter(Boolean);
    const route = String(parts[0] || "").toLowerCase();
    let raw = "";

    // Only treat shapes that positively identify a conversation as resource
    // references. In particular, #search/<query> is user-entered search content
    // and must never be mistaken for a thread locator merely because it looks
    // token-like. Search-result conversations have at least one segment between
    // the route name and the final thread token.
    if (GMAIL_DIRECT_THREAD_ROUTES.has(route) && parts.length === 2) {
      raw = parts[1] || "";
    } else if (route === "search" && parts.length >= 3) {
      raw = parts[parts.length - 1] || "";
    } else {
      return null;
    }

    if (!/^[A-Za-z0-9_-]{12,80}$/.test(raw)) return null;
    return candidate("gmail", "thread_locator", `web-thread:${raw}`, u.hostname);
  }

  function parseGithub(u) {
    if (u.hostname !== "github.com") return null;
    const parts = u.pathname.split("/").filter(Boolean);
    if (parts.length < 4 || !GITHUB_PART.test(parts[0] || "") || !GITHUB_PART.test(parts[1] || "")) return null;
    const number = parts[3] || "";
    if (!/^[1-9][0-9]{0,9}$/.test(number)) return null;
    if (parts[2] === "pull") return candidate("github", "pull_request", `${parts[0]}/${parts[1]}/pull/${number}`, u.hostname);
    if (parts[2] === "issues") return candidate("github", "issue", `${parts[0]}/${parts[1]}/issues/${number}`, u.hostname);
    return null;
  }

  function parseSalesforce(u) {
    const host = u.hostname.toLowerCase();
    if (!(host.endsWith(".salesforce.com") || host.endsWith(".force.com"))) return null;
    const parts = u.pathname.split("/").filter(Boolean);
    const r = parts.indexOf("r");
    if (r < 0 || r + 2 >= parts.length) return null;
    const objectType = parts[r + 1] || "";
    const recordId = parts[r + 2] || "";
    if (!/^[A-Za-z][A-Za-z0-9_]{0,79}$/.test(objectType) || !SALESFORCE_ID.test(recordId)) return null;
    return candidate("salesforce", "record", `${objectType}:${recordId}`, host);
  }

  function parseJira(u) {
    const host = u.hostname.toLowerCase();
    if (!host.endsWith(".atlassian.net")) return null;
    const parts = u.pathname.split("/").filter(Boolean);
    const browse = parts.indexOf("browse");
    const key = browse >= 0 ? (parts[browse + 1] || "") : "";
    if (!ISSUE_KEY.test(key)) return null;
    return candidate("jira", "issue", key, host);
  }

  function parseLinear(u) {
    if (u.hostname !== "linear.app") return null;
    const parts = u.pathname.split("/").filter(Boolean);
    const issue = parts.indexOf("issue");
    if (issue <= 0) return null;
    const workspace = parts[issue - 1] || "";
    const key = parts[issue + 1] || "";
    if (!WORKSPACE.test(workspace) || !ISSUE_KEY.test(key)) return null;
    return candidate("linear", "issue", `${workspace}:${key}`, u.hostname);
  }

  function parse(raw) {
    let u;
    try {
      u = new URL(String(raw || ""));
    } catch (_) {
      return null;
    }
    if (u.protocol !== "http:" && u.protocol !== "https:") return null;
    return parseGoogleWorkspace(u) || parseGmail(u) || parseGithub(u) || parseSalesforce(u) || parseJira(u) || parseLinear(u);
  }

  const api = {parse};
  root.OpenWorkGraphResourceReferences = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
