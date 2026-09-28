from __future__ import annotations

"""Content-free structural detail about an agent's tool call.

Adapters see tool inputs and outputs in memory (a shell command, a file path,
an edit patch, test output). This module turns them into a few structural facts
and nothing else:

* ``commands``: which well-known tools ran (``pytest``, ``git``, ``npm``), taken
  from a fixed allowlist; arguments, paths and anything unknown are dropped.
* ``git`` / ``gh``: which well-known git or GitHub CLI operations ran
  (``commit``, ``push``, ``pr_create``), again allowlisted.
* ``tests_passed`` / ``tests_failed``: counts parsed from a recognised test
  runner's summary line. Only the numbers leave this module.
* ``test_status``: ``unknown`` only when a test invocation was observed but no
  recognised result summary was available; known pass/fail is derived from counts.
* ``file_types``: allowlisted file extensions (``py``, ``ts``, ``md``).
* ``file_refs``: keyed hashes of file paths. They show "the same file again"
  without revealing the path; the key never leaves this computer.
* ``lines_added`` / ``lines_removed``: counts from an edit's patch.

``sanitize_detail`` is the single gate every value passes before it is stored,
so a buggy or hostile adapter cannot smuggle text through these fields.
"""

import hashlib
import hmac
import os
import posixpath
import re
import shlex
from typing import Any, Iterable

MAX_ITEMS = 8
MAX_COUNT = 1_000_000
_FILE_REF_RE = re.compile(r"^f:[0-9a-f]{16}$")

COMMANDS = frozenset({
    # languages / runtimes / package managers
    "python", "pip", "uv", "poetry", "pipenv", "conda", "node", "npm", "npx", "pnpm", "yarn", "bun", "deno",
    "go", "cargo", "rustc", "java", "javac", "mvn", "gradle", "dotnet", "ruby", "bundle", "gem", "php",
    "composer", "swift", "xcodebuild", "make", "cmake", "ninja", "bazel",
    # tests / linters / formatters / type checkers
    "pytest", "unittest", "tox", "nox", "jest", "vitest", "mocha", "playwright", "cypress", "ruff", "black",
    "isort", "flake8", "pylint", "mypy", "pyright", "eslint", "prettier", "tsc", "biome", "rubocop",
    "golangci-lint", "clippy",
    # version control / hosting
    "git", "gh", "glab",
    # containers / infra / cloud
    "docker", "podman", "kubectl", "helm", "terraform", "aws", "gcloud", "az", "vercel", "netlify", "fly",
    # data / db
    "psql", "sqlite3", "mysql", "redis-cli",
    # common shell utilities
    "ls", "cat", "head", "tail", "grep", "rg", "find", "fd", "sed", "awk", "jq", "curl", "wget", "cp", "mv",
    "rm", "mkdir", "touch", "chmod", "tar", "zip", "unzip", "diff", "wc", "sort", "echo", "cd", "pwd",
    "which", "open", "ps", "kill", "pkill", "lsof", "sqlite",
})
_RUNNER_ALIASES = {"python3": "python", "pip3": "pip", "nodejs": "node"}
GIT_OPS = frozenset({
    "add", "am", "bisect", "blame", "branch", "checkout", "cherry-pick", "clean", "clone", "commit", "config",
    "diff", "fetch", "grep", "init", "log", "merge", "mv", "pull", "push", "rebase", "reflog", "remote",
    "reset", "restore", "revert", "rm", "show", "stash", "status", "submodule", "switch", "tag", "worktree",
})
GH_OPS = frozenset({
    "pr_create", "pr_merge", "pr_view", "pr_checks", "pr_list", "pr_checkout", "pr_diff", "pr_review",
    "pr_comment", "pr_close", "pr_edit", "pr_ready", "issue_create", "issue_view", "issue_list",
    "issue_comment", "issue_close", "run_view", "run_list", "run_watch", "run_rerun", "repo_view",
    "repo_clone", "release_create", "release_view", "api",
})
FILE_TYPES = frozenset({
    "py", "pyi", "ipynb", "js", "mjs", "cjs", "jsx", "ts", "tsx", "go", "rs", "java", "kt", "swift", "c",
    "h", "cc", "cpp", "hpp", "cs", "rb", "php", "scala", "sql", "sh", "bash", "zsh", "ps1", "html", "css",
    "scss", "vue", "svelte", "json", "yaml", "yml", "toml", "ini", "cfg", "env", "xml", "md", "mdx", "rst",
    "txt", "csv", "tsv", "lock", "dockerfile", "makefile", "tf", "proto", "graphql", "r", "jl", "lua",
})
TEST_STATUSES = frozenset({"passing", "failing", "unknown"})
_WRAPPERS = {"sudo", "time", "env", "nohup", "exec", "command", "builtin", "nice"}
_WRAPPER_VALUE_OPTIONS = {
    "sudo": {"-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-U", "-T"},
    "env": {"-u", "-C", "-S"},
    "nice": {"-n"},
    "exec": {"-a"},
}


# ------------------------------------------------------------------ commands

def _words(segment: str) -> list[str]:
    try:
        return shlex.split(segment, posix=True)
    except ValueError:
        return segment.split()


def _command_segments(command: Any) -> list[list[str]]:
    if isinstance(command, (list, tuple)):
        words = [str(w) for w in command]
        # ["bash", "-lc", "pytest -q"] runs the inner string.
        if len(words) >= 3 and posixpath.basename(words[0]) in {"bash", "sh", "zsh"} and words[1] in {"-c", "-lc", "-ic"}:
            return _command_segments(words[2])
        return [words] if words else []
    text = str(command or "")[:20_000]
    try:
        # Quote-aware: separators inside a quoted commit message or echo string
        # do not start a new command.
        lexer = shlex.shlex(text, posix=True, punctuation_chars=";&|\n")
        lexer.whitespace = " \t\r"
        lexer.whitespace_split = True
        segments: list[list[str]] = [[]]
        for token in lexer:
            if token and set(token) <= set(";&|\n"):
                segments.append([])
            else:
                segments[-1].append(token)
        return [words for words in segments if words]
    except ValueError:
        # Malformed quoting is untrusted input. Do not regex-split separators,
        # because a separator may be inside the unterminated quote and would
        # invent a command that never actually executed.
        words = text.split()
        return [words] if words else []


def _program(words: list[str]) -> tuple[str, list[str]]:
    i = 0
    while i < len(words):
        word = words[i]
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", word):
            i += 1
        elif word in _WRAPPERS:
            i += 1
            # The wrapper's own options: "sudo -u bob git push", "nice -n 5 make".
            while i < len(words) and words[i].startswith("-") and words[i] != "--":
                i += 2 if words[i] in _WRAPPER_VALUE_OPTIONS.get(word, ()) else 1
            if i < len(words) and words[i] == "--":
                i += 1
        else:
            break
    if i >= len(words):
        return "", []
    name = posixpath.basename(words[i].replace("\\", "/")).lower()
    name = re.sub(r"\.exe$", "", name)
    name = _RUNNER_ALIASES.get(name, name)
    if re.fullmatch(r"python\d+(\.\d+)?", name):
        name = "python"
    rest = words[i + 1:]
    # python -m pytest / uv run pytest / npx jest / poetry run pytest
    if name == "python" and len(rest) >= 2 and rest[0] == "-m":
        return (rest[1] if rest[1] in COMMANDS else "python"), rest[2:]
    if name in {"uv", "poetry", "pipenv", "npx", "bunx", "pnpm", "yarn"} and rest:
        inner = rest[1:] if rest[0] in {"run", "exec", "dlx"} else rest
        if inner and posixpath.basename(inner[0]).lower() in COMMANDS - {"python"}:
            return posixpath.basename(inner[0]).lower(), inner[1:]
    return name, rest


def _first_positional(args: list[str], *, skip_with_value: Iterable[str] = ()) -> str:
    skip = set(skip_with_value)
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in skip:
            i += 2
            continue
        if arg.startswith("-"):
            i += 1
            continue
        return arg
    return ""


def command_detail(command: Any) -> dict[str, Any]:
    """Allowlisted programs, git and gh operations, and whether tests ran."""
    commands: list[str] = []
    git: list[str] = []
    gh: list[str] = []
    runs_tests = False
    for words in _command_segments(command):
        name, args = _program(words)
        if name not in COMMANDS:
            continue
        if name not in commands:
            commands.append(name)
        if name == "git":
            op = _first_positional(args, skip_with_value=("-C", "-c", "--git-dir", "--work-tree"))
            if op in GIT_OPS and op not in git:
                git.append(op)
        elif name == "gh":
            group = _first_positional(args, skip_with_value=("-R", "--repo"))
            if group == "api":
                op = "api"
            else:
                rest = args[args.index(group) + 1:] if group in args else []
                op = f"{group}_{_first_positional(rest)}" if group else ""
            if op in GH_OPS and op not in gh:
                gh.append(op)
        if name in {"pytest", "unittest", "tox", "nox", "jest", "vitest", "mocha", "playwright"}:
            runs_tests = True
        elif name in {"npm", "pnpm", "yarn", "bun"} and args[:1] in (["test"], ["t"]):
            runs_tests = True
        elif name in {"npm", "pnpm", "yarn", "bun"} and args[:2] == ["run", "test"]:
            runs_tests = True
        elif name in {"go", "cargo", "dotnet", "swift"} and args[:1] == ["test"]:
            runs_tests = True
        elif name == "node" and "--test" in args:
            runs_tests = True
    return {"commands": commands, "git": git, "gh": gh, "runs_tests": runs_tests}


# ------------------------------------------------------------------ tests

_SUMMARY_PATTERNS = (
    # pytest: "=== 2 failed, 40 passed, 1 error in 3.1s ===" or "40 passed in 0.5s"
    re.compile(r"(?P<body>(?:\d+ (?:passed|failed|errors?|skipped|deselected|xfailed|xpassed|warnings?)(?:, )?)+) in [\d.]+s"),
    # jest: "Tests:       1 failed, 5 passed, 6 total"
    re.compile(r"Tests:\s+(?P<body>[^\n]*\d+ total)"),
    # vitest: "Tests  1 failed | 5 passed (6)"
    re.compile(r"Tests\s+(?P<body>(?:\d+ (?:passed|failed|skipped|todo)(?: \| )?)+)\s*\(\d+\)"),
    # cargo: "test result: ok. 12 passed; 0 failed;"
    re.compile(r"test result: \w+\. (?P<body>\d+ passed; \d+ failed)"),
    # node --test: "ℹ pass 12" + "ℹ fail 0" (or "# pass 12" / "# fail 0")
    re.compile(r"(?:ℹ|#) pass (?P<passed>\d+)\s*[\r\n]+(?:.*[\r\n]+)*?(?:ℹ|#) fail (?P<failed>\d+)"),
)
_UNITTEST_RAN = re.compile(r"Ran (\d+) tests? in [\d.]+s")
_UNITTEST_FAILED = re.compile(r"FAILED \((?:failures=(\d+))?(?:, )?(?:errors=(\d+))?")


def _bounded(value: Any) -> int | None:
    try:
        number = int(value)
    except Exception:
        return None
    return number if 0 <= number <= MAX_COUNT else None


def test_counts(output: Any) -> dict[str, int]:
    """Passed/failed counts from the last recognised test-runner summary."""
    text = str(output or "")[-200_000:]
    for pattern in _SUMMARY_PATTERNS:
        matches = list(pattern.finditer(text))
        if not matches:
            continue
        match = matches[-1]
        groups = match.groupdict()
        if groups.get("passed") is not None:
            passed, failed = _bounded(groups["passed"]), _bounded(groups["failed"])
            return {k: v for k, v in (("tests_passed", passed), ("tests_failed", failed)) if v is not None}
        body = groups.get("body") or ""
        passed = sum(int(n) for n in re.findall(r"(\d+) passed", body))
        failed = sum(int(n) for n in re.findall(r"(\d+) (?:failed|errors?)\b", body))
        return {"tests_passed": min(passed, MAX_COUNT), "tests_failed": min(failed, MAX_COUNT)}
    ran = list(_UNITTEST_RAN.finditer(text))
    if ran:
        total = int(ran[-1].group(1))
        failed_match = list(_UNITTEST_FAILED.finditer(text[ran[-1].end():]))
        failed = 0
        if failed_match:
            failed = sum(int(x) for x in failed_match[-1].groups() if x)
        return {"tests_passed": max(0, min(total - failed, MAX_COUNT)), "tests_failed": min(failed, MAX_COUNT)}
    return {}


# ------------------------------------------------------------------ files

def _file_ref_key() -> bytes:
    from server.local_auth import _read_or_create_secret

    return _read_or_create_secret(".agent_file_ref_key").encode("utf-8")


def file_ref(path: Any, *, key: bytes | None = None) -> str:
    raw = str(path or "").strip()
    if not raw:
        return ""
    normalized = posixpath.normpath(raw.replace("\\", "/"))
    digest = hmac.new(key if key is not None else _file_ref_key(), normalized.encode("utf-8"), hashlib.sha256)
    return "f:" + digest.hexdigest()[:16]


def file_type(path: Any) -> str:
    name = posixpath.basename(str(path or "").replace("\\", "/")).lower()
    if not name:
        return ""
    if name in {"dockerfile", "makefile"}:
        return name
    ext = name.rsplit(".", 1)[-1] if "." in name.strip(".") else ""
    return ext if ext in FILE_TYPES else ""


def patch_line_counts(patch: Any) -> tuple[int, int]:
    """Lines added/removed from a structured patch (list of hunks) or unified diff text."""
    lines: list[str] = []
    if isinstance(patch, list):
        for hunk in patch:
            if isinstance(hunk, dict) and isinstance(hunk.get("lines"), list):
                lines.extend(str(x) for x in hunk["lines"])
    elif isinstance(patch, str):
        lines = patch.splitlines()
    added = sum(1 for line in lines if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in lines if line.startswith("-") and not line.startswith("---"))
    return min(added, MAX_COUNT), min(removed, MAX_COUNT)


def apply_patch_detail(patch: Any, *, key: bytes | None = None) -> dict[str, Any]:
    """Codex apply_patch envelopes: files touched and lines changed."""
    text = str(patch or "")
    paths = re.findall(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", text, flags=re.MULTILINE)
    body = "\n".join(line for line in text.splitlines() if not line.startswith("***"))
    added, removed = patch_line_counts(body)
    return files_detail(paths, key=key) | {"lines_added": added, "lines_removed": removed}


def files_detail(paths: Iterable[Any], *, key: bytes | None = None) -> dict[str, Any]:
    refs: list[str] = []
    types: list[str] = []
    for path in paths:
        ref = file_ref(path, key=key)
        if ref and ref not in refs:
            refs.append(ref)
        kind = file_type(path)
        if kind and kind not in types:
            types.append(kind)
    return {"file_refs": refs[:MAX_ITEMS], "file_types": types[:MAX_ITEMS]}


# ------------------------------------------------------------------ the gate

def sanitize_detail(raw: Any) -> dict[str, Any]:
    """Keep only allowlisted, well-formed values. Everything else is dropped."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}

    def listed(key: str, allowed: frozenset[str] | None = None, pattern: re.Pattern | None = None) -> None:
        values = raw.get(key)
        if not isinstance(values, (list, tuple)):
            return
        kept: list[str] = []
        for value in values:
            text = str(value)
            if (allowed is not None and text in allowed) or (pattern is not None and pattern.fullmatch(text)):
                if text not in kept:
                    kept.append(text)
        if kept:
            out[key] = kept[:MAX_ITEMS]

    listed("commands", COMMANDS)
    listed("git", GIT_OPS)
    listed("gh", GH_OPS)
    listed("file_types", FILE_TYPES)
    listed("file_refs", pattern=_FILE_REF_RE)
    test_status = str(raw.get("test_status") or "")
    if test_status in TEST_STATUSES:
        out["test_status"] = test_status
    for key in ("lines_added", "lines_removed", "tests_passed", "tests_failed"):
        value = raw.get(key)
        if isinstance(value, bool):
            continue
        number = _bounded(value)
        if number is not None:
            out[key] = number
    return out


def tool_call_detail(
    *,
    command: Any = None,
    output: Any = None,
    paths: Iterable[Any] = (),
    patch: Any = None,
    new_file_text: Any = None,
    key: bytes | None = None,
) -> dict[str, Any]:
    """Build sanitized detail from what an adapter holds in memory."""
    detail: dict[str, Any] = {}
    if command is not None:
        info = command_detail(command)
        detail.update({k: info[k] for k in ("commands", "git", "gh") if info[k]})
        if info["runs_tests"]:
            counts = test_counts(output)
            if counts:
                detail.update(counts)
            else:
                detail["test_status"] = "unknown"
    paths = [p for p in paths if p]
    if paths:
        try:
            detail.update(files_detail(paths, key=key))
        except Exception:
            pass  # no key available: skip refs rather than fail the event
    if patch is not None:
        added, removed = patch_line_counts(patch)
        if added or removed:
            detail.update(lines_added=added, lines_removed=removed)
    elif new_file_text is not None:
        text = str(new_file_text)
        detail.update(lines_added=min(len(text.splitlines()), MAX_COUNT), lines_removed=0)
    return sanitize_detail(detail)


def enabled() -> bool:
    return os.getenv("OWG_AGENT_TOOL_DETAIL", "1").strip().lower() not in {"0", "false", "off", "no"}
