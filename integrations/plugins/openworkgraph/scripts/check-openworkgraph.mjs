const controller = new AbortController();
const timer = setTimeout(() => controller.abort(), 1200);
let running = false;
try {
  const response = await fetch("http://127.0.0.1:8787/health", { signal: controller.signal });
  if (response.ok) {
    const body = await response.json();
    running = body?.status === "ok";
  }
} catch (_) {
  running = false;
} finally {
  clearTimeout(timer);
}
if (!running) {
  const text = [
    "OpenWorkGraph is not currently running.",
    "Start OpenWorkGraph locally before relying on OWG context.",
    "If it is not installed, install/launch it from the OpenWorkGraph GitHub release or your organization-provided installer, then reopen Claude Code."
  ].join(" ");
  process.stdout.write(JSON.stringify({
    hookSpecificOutput: {
      hookEventName: "SessionStart",
      additionalContext: text
    }
  }) + "\n");
}
