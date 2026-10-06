import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";

function roots() {
  const out = [];
  if (process.env.OWG_HOME) out.push(process.env.OWG_HOME);
  if (process.platform === "darwin") {
    out.push(path.join(os.homedir(), "Library", "Application Support", "WorkflowObserver"));
  } else if (process.platform === "win32") {
    if (process.env.LOCALAPPDATA) out.push(path.join(process.env.LOCALAPPDATA, "OpenWorkGraph"));
  }
  out.push(process.cwd());
  return [...new Set(out)];
}

function runtime(root) {
  if (process.platform === "win32") return path.join(root, ".venv", "Scripts", "python.exe");
  return path.join(root, ".venv", "bin", "python");
}

const root = roots().find(candidate =>
  fs.existsSync(path.join(candidate, "mcp_server", "launcher.py")) &&
  fs.existsSync(runtime(candidate))
);

if (!root) {
  process.stderr.write(
    "OpenWorkGraph is not installed/runnable locally. Start the OpenWorkGraph installer/launcher once, then reopen Claude Code.\n"
  );
  process.exit(2);
}

const child = spawn(
  runtime(root),
  ["-m", "mcp_server.launcher", "--client", "claude_code"],
  { cwd: root, stdio: "inherit", env: { ...process.env, OWG_HOME: root } }
);
child.on("exit", code => process.exit(code ?? 0));
child.on("error", error => {
  process.stderr.write(`Could not launch OpenWorkGraph MCP: ${error.message}\n`);
  process.exit(2);
});
