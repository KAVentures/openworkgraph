const { spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

function installedRoot() {
  if (process.platform === 'darwin') {
    return path.join(process.env.HOME || '', 'Library', 'Application Support', 'WorkflowObserver');
  }
  if (process.platform === 'win32') {
    return path.join(process.env.LOCALAPPDATA || '', 'OpenWorkGraph');
  }
  return '';
}

function pythonPath(root) {
  return process.platform === 'win32'
    ? path.join(root, '.venv', 'Scripts', 'python.exe')
    : path.join(root, '.venv', 'bin', 'python');
}

const root = installedRoot();
const python = pythonPath(root);
if (!root || !fs.existsSync(python)) {
  console.error('OpenWorkGraph is not installed. Install and start OpenWorkGraph before using this Claude extension.');
  process.exit(1);
}

const env = {
  ...process.env,
  PYTHONPATH: root,
  WORKFLOW_OBSERVER_API: 'http://127.0.0.1:8787',
  WORKFLOW_OBSERVER_AUTH_DIR: path.join(root, 'data', 'auth'),
};

const child = spawn(python, ['-m', 'mcp_server.compact_stdio'], {
  cwd: root,
  env,
  stdio: ['inherit', 'inherit', 'inherit'],
  windowsHide: true,
});

child.on('error', (error) => {
  console.error(`Could not start OpenWorkGraph MCP: ${error.message}`);
  process.exit(1);
});
child.on('exit', (code, signal) => {
  if (signal) process.kill(process.pid, signal);
  else process.exit(code == null ? 1 : code);
});
