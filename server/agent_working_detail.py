from __future__ import annotations

"""Opt-in local working detail for cross-agent continuity.

This store is deliberately separate from canonical OpenWorkGraph evidence. It keeps
small factual state that is useful when one agent continues another's work:
workspace-relative file paths, structured command/test facts, bounded redacted
error excerpts, and repository state. Raw tool output, arbitrary shell arguments,
absolute paths, prompts, messages and hidden reasoning are never persisted here.

The native-session scanner is independent of the structural session sensor so a
parser failure cannot affect canonical evidence capture. Normal enablement starts
at the current EOF; historical import is a separate explicit operation.
"""

import hashlib
import hmac
import json
import os
import posixpath
import re
import shlex
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .ai_context import redact_contextually
from .db import DATA_DIR, connect
from .local_auth import _read_or_create_secret
from .agent_session_store import native_file_ref, read_agent_session_policy, session_ref, upsert_session
from shared.tool_detail import command_detail, test_counts, workspace_ref

_POLICY_PATH = DATA_DIR / "agent_working_detail_policy.json"
_STATE_PATH = DATA_DIR / "agent_working_detail_state.json"
_DEFAULT_POLICY: dict[str, Any] = {"capture_working_detail": False, "allow_ai_read_working_detail": False, "working_detail_retention_days": 30, "sources": {"claude_code": True, "codex": True}}
_ALLOWED_SOURCES = {"claude_code", "codex"}
_MAX_FACTS = 100_000
_MAX_PATHS = 24
_MAX_FAILING_TESTS = 12
_MAX_EXCERPT = 280
_MAX_LINE_BYTES = 2 * 1024 * 1024
_LOCK = threading.RLock()
_STOP = threading.Event()
_THREAD: threading.Thread | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_working_detail (
  detail_ref TEXT PRIMARY KEY,
  session_ref TEXT NOT NULL,
  source TEXT NOT NULL,
  workspace_ref TEXT NOT NULL DEFAULT '',
  observed_at TEXT NOT NULL,
  kind TEXT NOT NULL,
  facts_json TEXT NOT NULL,
  provenance_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_agent_working_detail_session ON agent_working_detail(session_ref, observed_at, detail_ref);
CREATE INDEX IF NOT EXISTS idx_agent_working_detail_time ON agent_working_detail(observed_at);
"""

_TEST_RUNNERS = {"pytest", "jest", "vitest", "mocha", "playwright", "unittest", "tox", "nox", "cargo", "go", "npm", "pnpm", "yarn", "bun"}
_FAILURE_ID_RE = re.compile(r"(?m)^FAILED\s+([^\s]+?)(?:\s+-|$)")
_JEST_FAIL_RE = re.compile(r"(?m)^\s*FAIL\s+([^\s]+)")
_ERROR_TYPE_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception|Failure))\b")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _secret() -> bytes:
    return _read_or_create_secret(".agent_working_detail_key").encode("utf-8")


def _opaque(prefix: str, value: Any, chars: int = 24) -> str:
    digest = hmac.new(_secret(), str(value or "").encode("utf-8", errors="ignore"), hashlib.sha256).hexdigest()[:chars]
    return f"{prefix}:{digest}"


def detail_ref(source: str, native_session: Any, native_call: Any, kind: str = "tool") -> str:
    return _opaque("awd", f"{source}|{native_session}|{native_call}|{kind}")


def _redact_text(value: Any, *, limit: int) -> str:
    raw = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(value or ""))[: max(limit * 4, limit)]
    if not raw.strip():
        return ""
    try:
        protected = redact_contextually({"value": raw})
        return str((protected or {}).get("value") or "").strip()[:limit] if isinstance(protected, dict) else ""
    except Exception:
        return ""


def _normalize_policy(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    out = json.loads(json.dumps(_DEFAULT_POLICY))
    for key in ("capture_working_detail", "allow_ai_read_working_detail"):
        if key in raw:
            out[key] = bool(raw[key])
    try:
        days = int(raw.get("working_detail_retention_days", out["working_detail_retention_days"]))
    except Exception:
        days = int(out["working_detail_retention_days"])
    out["working_detail_retention_days"] = max(1, min(days, 3650))
    if isinstance(raw.get("sources"), dict):
        for source in _ALLOWED_SOURCES:
            if source in raw["sources"]:
                out["sources"][source] = bool(raw["sources"][source])
    if not out["capture_working_detail"]:
        out["allow_ai_read_working_detail"] = False
    return out


def read_policy() -> dict[str, Any]:
    try:
        value = json.loads(_POLICY_PATH.read_text(encoding="utf-8"))
    except Exception:
        value = {}
    return _normalize_policy(value)


def _write_policy(policy: dict[str, Any]) -> None:
    _POLICY_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _POLICY_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(policy, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try: os.chmod(tmp, 0o600)
    except Exception: pass
    os.replace(tmp, _POLICY_PATH)
    try: os.chmod(_POLICY_PATH, 0o600)
    except Exception: pass


def write_policy(value: dict[str, Any]) -> dict[str, Any]:
    old = read_policy(); policy = _normalize_policy(value)
    if policy["capture_working_detail"] and not old["capture_working_detail"]:
        _bootstrap_existing_files()
    _write_policy(policy); cleanup(); return policy


def init_store() -> None:
    with connect() as conn: conn.executescript(_SCHEMA)
    cleanup()


def cleanup(*, now: datetime | None = None) -> int:
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=int(read_policy()["working_detail_retention_days"]))
    with connect() as conn:
        cur = conn.execute("DELETE FROM agent_working_detail WHERE observed_at < ?", (cutoff.isoformat().replace("+00:00", "Z"),))
        return int(cur.rowcount or 0)


def delete_all() -> int:
    with connect() as conn:
        cur = conn.execute("DELETE FROM agent_working_detail"); return int(cur.rowcount or 0)


def _safe_relative_path(path: Any, workspace: Any) -> str:
    raw, root_raw = str(path or "").strip(), str(workspace or "").strip()
    if not raw or not root_raw or "\x00" in raw: return ""
    try:
        root = Path(root_raw).expanduser().resolve(strict=False)
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute(): candidate = root / candidate
        candidate = candidate.resolve(strict=False); relative = candidate.relative_to(root)
    except (OSError, RuntimeError, ValueError): return ""
    normalized = posixpath.normpath(relative.as_posix())
    if normalized in {"", ".", ".."} or normalized.startswith("../") or normalized.startswith("/"): return ""
    return _redact_text(normalized, limit=320)


def _safe_paths(values: Iterable[Any], workspace: Any) -> list[str]:
    out=[]
    for value in values:
        item=_safe_relative_path(value, workspace)
        if item and item not in out: out.append(item)
        if len(out)>=_MAX_PATHS: break
    return out


def _command_words(command: Any) -> list[str]:
    text=str(command or "")[:20_000]
    try: return shlex.split(text, posix=os.name != "nt")
    except Exception: return text.split()


def _looks_like_safe_target(word: str) -> bool:
    candidate=word.split("::",1)[0]; low=candidate.lower()
    return "/" in candidate or "\\" in candidate or "::" in word or low.endswith((".py",".js",".ts",".tsx",".jsx",".go",".rs",".java",".rb",".php",".swift"))


def _structured_command(command: Any, workspace: Any) -> dict[str, Any]:
    structural=command_detail(command); commands=list(structural.get("commands") or [])
    if not commands: return {}
    program=str(commands[-1]); out={"program":program}
    if program in _TEST_RUNNERS:
        targets=[]
        for word in _command_words(command)[1:]:
            if not word or word.startswith("-") or "=" in word or not _looks_like_safe_target(word): continue
            candidate=word.split("::",1)[0]; safe=_safe_relative_path(candidate,workspace)
            if safe and safe not in targets:
                targets.append((safe+word[len(candidate):])[:360])
            if len(targets)>=8: break
        if targets: out["targets"]=targets
    return out


def _extract_output_text(output: Any) -> str:
    if isinstance(output,str): return output[-200_000:]
    if isinstance(output,dict): return "\n".join(str(output.get(k) or "") for k in ("stdout","stderr","output","content","error") if isinstance(output.get(k),str))[-200_000:]
    if isinstance(output,list): return "\n".join(str(item.get("text") or item.get("content") or "") for item in output if isinstance(item,dict))[-200_000:]
    return ""


def _exit_code(value: Any) -> int | None:
    if not isinstance(value,dict): return None
    for key in ("exit_code","exitCode","code","returncode","status_code"):
        candidate=value.get(key)
        if isinstance(candidate,bool) or candidate is None: continue
        try: number=int(candidate)
        except Exception: continue
        if -255<=number<=65535: return number
    return None


def _bounded_error(text:str)->dict[str,Any]:
    for line in text.splitlines():
        line=line.strip(); match=_ERROR_TYPE_RE.search(line) if line else None
        if not match: continue
        excerpt=_redact_text(line,limit=_MAX_EXCERPT); out={"type":match.group(1)[:100]}
        if excerpt: out["excerpt"]={"text":excerpt,"untrusted_observed_text":True}
        return out
    return {}


def _failing_test_ids(text:str)->list[str]:
    out=[]
    for match in list(_FAILURE_ID_RE.finditer(text))+list(_JEST_FAIL_RE.finditer(text)):
        item=_redact_text(match.group(1),limit=320)
        if item and item not in out: out.append(item)
        if len(out)>=_MAX_FAILING_TESTS: break
    return out


def _test_facts(command:Any,output:Any)->dict[str,Any]:
    if not command_detail(command).get("runs_tests"): return {}
    text=_extract_output_text(output); counts=test_counts(text); facts={"status":"unknown"}
    if counts:
        passed=int(counts.get("tests_passed") or 0); failed=int(counts.get("tests_failed") or 0); facts.update(status="failing" if failed else "passing",passed=passed,failed=failed)
    failing=_failing_test_ids(text)
    if failing: facts["failing_tests"]=failing
    error=_bounded_error(text)
    if error: facts["error"]=error
    return facts


def _parsed_result_facts(output:Any)->dict[str,Any]:
    text=_extract_output_text(output)
    if not text: return {}
    facts={}; counts=test_counts(text)
    if counts:
        failed=int(counts.get("tests_failed") or 0); tests={"status":"failing" if failed else "passing","passed":int(counts.get("tests_passed") or 0),"failed":failed}; failing=_failing_test_ids(text)
        if failing: tests["failing_tests"]=failing
        facts["tests"]=tests
    error=_bounded_error(text)
    if error: facts["error"]=error
    code=_exit_code(output)
    if code is not None: facts["exit_code"]=code
    return facts


def _facts_from_tool(tool_name:Any,tool_input:Any,output:Any,workspace:Any,*,failed_hook:bool=False)->dict[str,Any]:
    name=str(tool_name or "")[:160]; inp=tool_input if isinstance(tool_input,dict) else {}; facts={"tool":name}; low=name.lower()
    if low in {"bash","shell","terminal","command","exec_command","bashoutput"}:
        command=inp.get("command") or inp.get("cmd"); structured=_structured_command(command,workspace)
        if structured: facts["command"]=structured
        tests=_test_facts(command,output)
        if tests: facts["tests"]=tests
    safe=_safe_paths([inp.get(k) for k in ("file_path","path","notebook_path") if isinstance(inp.get(k),str)],workspace)
    if safe: facts["files"]={"paths":safe,"operation":"edit" if any(x in low for x in ("edit","write","patch")) else "read"}
    code=_exit_code(output)
    if code is not None: facts["exit_code"]=code
    elif failed_hook: facts["exit_status"]="error_observed"
    error=_bounded_error(_extract_output_text(output))
    if error: facts.setdefault("error",error)
    return facts if len(facts)>1 else {}


def _merge_facts(old:dict[str,Any],new:dict[str,Any])->dict[str,Any]:
    out=json.loads(json.dumps(old)) if isinstance(old,dict) else {}
    for key,value in new.items():
        if key=="tests" and isinstance(value,dict):
            previous=out.get("tests") if isinstance(out.get("tests"),dict) else {}; merged={**previous,**value}
            if previous.get("status") in {"passing","failing"} and value.get("status")=="unknown": merged["status"]=previous["status"]
            out[key]=merged
        elif key=="files" and isinstance(value,dict):
            previous=out.get("files") if isinstance(out.get("files"),dict) else {}; paths=list(previous.get("paths") or [])
            for path in value.get("paths") or []:
                if path not in paths and len(paths)<_MAX_PATHS: paths.append(path)
            out[key]={**previous,**value,"paths":paths}
        else: out[key]=value
    return out


def upsert_detail(*,ref:str,session:str,source:str,workspace:str,observed_at:str,kind:str,facts:dict[str,Any],provenance:dict[str,Any])->bool:
    if not facts or not ref.startswith("awd:") or not session.startswith("as:") or source not in _ALLOWED_SOURCES: return False
    encoded=json.dumps(facts,ensure_ascii=False,sort_keys=True)
    if len(encoded)>32_000: return False
    prov=json.dumps(provenance,ensure_ascii=False,sort_keys=True)[:8_000]
    session_policy=read_agent_session_policy(); upsert_session(session_ref_value=session,source=source,client_id=source,workspace_ref=workspace,native_file_ref_value="",started_at=observed_at,updated_at=observed_at,message_capture_enabled=bool(session_policy.get("capture_visible_messages")))
    with connect() as conn:
        row=conn.execute("SELECT facts_json FROM agent_working_detail WHERE detail_ref = ?",(ref,)).fetchone()
        if row:
            try: old=json.loads(row[0])
            except Exception: old={}
            encoded=json.dumps(_merge_facts(old,facts),ensure_ascii=False,sort_keys=True)
        conn.execute("""INSERT INTO agent_working_detail(detail_ref,session_ref,source,workspace_ref,observed_at,kind,facts_json,provenance_json) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(detail_ref) DO UPDATE SET facts_json=excluded.facts_json,provenance_json=excluded.provenance_json,observed_at=CASE WHEN excluded.observed_at > agent_working_detail.observed_at THEN excluded.observed_at ELSE agent_working_detail.observed_at END,updated_at=CURRENT_TIMESTAMP""",(ref,session,source,str(workspace or "")[:64],str(observed_at or _now())[:80],str(kind or "tool")[:40],encoded,prov))
    return True


def details_for_session(session:str,*,limit:int=40)->list[dict[str,Any]]:
    if not read_policy().get("capture_working_detail"): return []
    with connect() as conn: rows=conn.execute("SELECT detail_ref,observed_at,kind,facts_json,provenance_json FROM agent_working_detail WHERE session_ref=? ORDER BY observed_at DESC,detail_ref DESC LIMIT ?",(session,max(1,min(int(limit),100)))).fetchall()
    out=[]
    for row in reversed(rows):
        try: facts=json.loads(row[3]); provenance=json.loads(row[4])
        except Exception: continue
        out.append({"detail_ref":row[0],"observed_at":row[1],"kind":row[2],"facts":facts,"provenance":provenance,"authoritative":False})
    return out


def _git(cwd:str,*args:str)->str:
    result=subprocess.run(["git","-c","core.fsmonitor=false","-c","core.hooksPath="+("NUL" if os.name=="nt" else "/dev/null"),"-C",cwd,*args],capture_output=True,text=True,timeout=2,check=False,env={**os.environ,"GIT_OPTIONAL_LOCKS":"0"})
    return result.stdout if result.returncode==0 else ""


def repository_state(cwd:Any)->dict[str,Any]:
    root_raw=str(cwd or "").strip()
    if not root_raw: return {}
    root=_git(root_raw,"rev-parse","--show-toplevel").strip()
    if not root: return {}
    branch=_redact_text(_git(root,"symbolic-ref","--quiet","--short","HEAD").strip(),limit=160); head=_git(root,"rev-parse","--short=12","HEAD").strip(); status=_git(root,"status","--porcelain=v1","--untracked-files=normal"); changed=[]; untracked=0
    for line in status.splitlines():
        if len(line)<4: continue
        code,raw_path=line[:2],line[3:]
        if " -> " in raw_path: raw_path=raw_path.split(" -> ",1)[1]
        safe=_safe_relative_path(raw_path.strip('"'),root)
        if safe and safe not in changed and len(changed)<_MAX_PATHS: changed.append(safe)
        if code=="??": untracked+=1
    out={"dirty":bool(status.strip()),"untracked_count":min(untracked,_MAX_FACTS)}
    if branch: out["branch"]=branch
    if re.fullmatch(r"[0-9a-fA-F]{7,40}",head): out["head"]=head.lower()
    if changed: out["changed_files"]=changed
    return out


def capture_claude_hook(payload:dict[str,Any])->bool:
    policy=read_policy()
    if not policy.get("capture_working_detail") or not policy.get("sources",{}).get("claude_code",True): return False
    hook=str(payload.get("hook_event_name") or ""); native_session=str(payload.get("session_id") or "").strip()
    if not native_session: return False
    session=session_ref("claude_code",native_session); cwd=str(payload.get("cwd") or ""); workspace=workspace_ref(cwd) if cwd else ""; observed=str(payload.get("timestamp") or _now())
    if hook in {"PostToolUse","PostToolUseFailure"}:
        call=str(payload.get("tool_use_id") or _opaque("call",observed)); raw_result=payload.get("tool_response") or payload.get("error"); facts=_facts_from_tool(payload.get("tool_name"),payload.get("tool_input"),raw_result,cwd,failed_hook=hook=="PostToolUseFailure")
        return upsert_detail(ref=detail_ref("claude_code",native_session,call),session=session,source="claude_code",workspace=workspace,observed_at=observed,kind="tool",facts=facts,provenance={"basis":"claude_hook_tool_input_and_result","parsed_output":bool(_extract_output_text(raw_result)),"raw_output_stored":False})
    if hook in {"Stop","SessionEnd"} and cwd:
        state=repository_state(cwd); return upsert_detail(ref=detail_ref("claude_code",native_session,str(payload.get("prompt_id") or hook),"repository"),session=session,source="claude_code",workspace=workspace,observed_at=observed,kind="repository_state",facts={"repository":state} if state else {},provenance={"basis":"local_git_snapshot","raw_output_stored":False})
    return False


def _load_state()->dict[str,Any]:
    try:
        value=json.loads(_STATE_PATH.read_text(encoding="utf-8")); return value if isinstance(value,dict) else {}
    except Exception: return {}


def _save_state(value:dict[str,Any])->None:
    _STATE_PATH.parent.mkdir(parents=True,exist_ok=True); tmp=_STATE_PATH.with_suffix(".tmp"); tmp.write_text(json.dumps(value,sort_keys=True)+"\n",encoding="utf-8")
    try: os.chmod(tmp,0o600)
    except Exception: pass
    os.replace(tmp,_STATE_PATH)


def _source_files(source:str)->list[Path]:
    from . import agent_session_sensor as native
    return native._source_files(native._SOURCE_SPECS[source])


def _file_key(path:Path)->str: return native_file_ref(path)


def _bootstrap_existing_files()->None:
    state=_load_state(); files=state.setdefault("files",{})
    for source in _ALLOWED_SOURCES:
        for path in _source_files(source):
            try:
                stat=path.stat(); files[_file_key(path)]={"source":source,"offset":stat.st_size,"size":stat.st_size,"mtime_ns":stat.st_mtime_ns}
            except OSError: continue
    state["boundary_at"]=_now(); _save_state(state)


def _claude_records(obj:dict[str,Any],native_session:str,workspace_path:str,observed:str)->list[tuple[str,dict[str,Any],dict[str,Any]]]:
    raw=(obj.get("message") or {}).get("content") if isinstance(obj.get("message"),dict) else obj.get("content")
    if not isinstance(raw,list): return []
    output=[]
    for i,item in enumerate(raw):
        if not isinstance(item,dict): continue
        typ=str(item.get("type") or "")
        if typ=="tool_use":
            call=str(item.get("id") or item.get("tool_use_id") or f"{observed}:{i}"); output.append((call,_facts_from_tool(item.get("name"),item.get("input"),None,workspace_path),{"basis":"native_session_tool_input","parsed_output":False,"raw_output_stored":False}))
        elif typ=="tool_result":
            call=str(item.get("tool_use_id") or item.get("id") or f"{observed}:{max(0,i-1)}"); raw_result=item.get("content") or item; output.append((call,_parsed_result_facts(raw_result),{"basis":"native_session_parsed_tool_result","parsed_output":bool(_extract_output_text(raw_result)),"raw_output_stored":False}))
    return output


def _codex_record(obj:dict[str,Any],workspace_path:str,observed:str)->tuple[str,dict[str,Any],dict[str,Any]]|None:
    if str(obj.get("type") or "")!="response_item": return None
    payload=obj.get("payload") if isinstance(obj.get("payload"),dict) else {}; subtype=str(payload.get("type") or ""); call=str(payload.get("call_id") or payload.get("id") or observed)
    if subtype in {"function_call","custom_tool_call"}:
        name=str(payload.get("name") or ""); raw_args=payload.get("arguments") if subtype=="function_call" else payload.get("input"); args=raw_args
        if isinstance(raw_args,str):
            try: args=json.loads(raw_args)
            except Exception: args={"command":raw_args} if name.lower() in {"shell","bash","exec_command"} else {}
        return call,_facts_from_tool(name,args if isinstance(args,dict) else {},None,workspace_path),{"basis":"native_session_tool_input","parsed_output":False,"raw_output_stored":False}
    if subtype in {"function_call_output","custom_tool_call_output"}:
        raw_output=payload.get("output") or payload.get("content") or payload; return call,_parsed_result_facts(raw_output),{"basis":"native_session_parsed_tool_result","parsed_output":bool(_extract_output_text(raw_output)),"raw_output_stored":False}
    return None


def _process_obj(source:str,obj:dict[str,Any],path:Path)->int:
    from .agent_session_sensor import _session_and_workspace
    session,workspace,native_session=_session_and_workspace(source,obj,path)
    if not session or not native_session: return 0
    workspace_path=str(obj.get("cwd") or "")
    if source=="codex":
        payload=obj.get("payload") if isinstance(obj.get("payload"),dict) else {}; workspace_path=str(payload.get("cwd") or obj.get("cwd") or "")
    observed=str(obj.get("timestamp") or obj.get("ts") or _now()); records=_claude_records(obj,native_session,workspace_path,observed) if source=="claude_code" else []
    if source=="codex":
        one=_codex_record(obj,workspace_path,observed); records=[one] if one else []
    return sum(1 for call,facts,provenance in records if upsert_detail(ref=detail_ref(source,native_session,call),session=session,source=source,workspace=workspace,observed_at=observed,kind="tool",facts=facts,provenance=provenance))


def scan_once()->dict[str,Any]:
    policy=read_policy()
    if not policy.get("capture_working_detail"): return {"enabled":False,"details_written":0}
    with _LOCK:
        state=_load_state(); files_state=state.setdefault("files",{}); written=errors=0
        for source in _ALLOWED_SOURCES:
            if not policy.get("sources",{}).get(source,True): continue
            for path in _source_files(source):
                key=_file_key(path)
                try:
                    stat=path.stat(); saved=files_state.get(key); offset=0 if not isinstance(saved,dict) else int(saved.get("offset") or 0)
                    if stat.st_size<offset: offset=0
                    with path.open("rb") as fh:
                        fh.seek(offset)
                        while True:
                            line=fh.readline(_MAX_LINE_BYTES+1)
                            if not line: break
                            if len(line)>_MAX_LINE_BYTES: errors+=1; continue
                            try: obj=json.loads(line.decode("utf-8"))
                            except Exception: continue
                            if isinstance(obj,dict): written+=_process_obj(source,obj,path)
                        offset=fh.tell()
                    files_state[key]={"source":source,"offset":offset,"size":stat.st_size,"mtime_ns":stat.st_mtime_ns}
                except (OSError,ValueError): errors+=1
        state["last_scan_at"]=_now(); _save_state(state); cleanup(); return {"enabled":True,"details_written":written,"errors":errors,"last_scan_at":state["last_scan_at"]}


def import_recent(*,days:int=7,include_structural:bool=True,include_working_detail:bool=True,include_visible_messages:bool=False)->dict[str,Any]:
    bounded_days=max(1,min(int(days),30)); cutoff=datetime.now(timezone.utc)-timedelta(days=bounded_days); session_policy=read_agent_session_policy(); working_policy=read_policy()
    if include_working_detail and not working_policy.get("capture_working_detail"): raise ValueError("Enable Working detail before importing it")
    if include_visible_messages and not session_policy.get("capture_visible_messages"): raise ValueError("Enable visible-message capture before importing messages")
    from . import agent_session_sensor as native
    from .agent_ingest import ingest_agent_payloads
    from .agent_session_store import insert_visible_messages
    totals={"files_scanned":0,"structural_events":0,"working_details":0,"visible_messages":0}
    for source in _ALLOWED_SOURCES:
        if not working_policy.get("sources",{}).get(source,True): continue
        spec=native._SOURCE_SPECS[source]
        for path in native._source_files(spec):
            totals["files_scanned"]+=1; ordinal=0
            try:
                with path.open("rb") as fh:
                    for raw in fh:
                        ordinal+=1
                        if len(raw)>_MAX_LINE_BYTES: continue
                        try: obj=json.loads(raw.decode("utf-8"))
                        except Exception: continue
                        if not isinstance(obj,dict): continue
                        observed=str(obj.get("timestamp") or obj.get("ts") or "")
                        try: parsed=datetime.fromisoformat(observed.replace("Z","+00:00")).astimezone(timezone.utc)
                        except Exception: continue
                        if parsed<cutoff: continue
                        session,workspace,_native=native._session_and_workspace(source,obj,path)
                        if include_working_detail: totals["working_details"]+=_process_obj(source,obj,path)
                        if include_structural or include_visible_messages:
                            parser=native._parse_claude if source=="claude_code" else native._parse_codex; structural,messages=parser(obj,spec,session,ordinal,workspace)
                            if structural and include_structural: totals["structural_events"]+=int(ingest_agent_payloads(structural).get("inserted") or 0)
                            if include_visible_messages and messages:
                                upsert_session(session_ref_value=session,source=source,client_id=spec["client_id"],workspace_ref=workspace,native_file_ref_value=native_file_ref(path),started_at=observed,updated_at=observed,message_capture_enabled=True); totals["visible_messages"]+=insert_visible_messages(session,messages)
            except OSError: continue
    totals.update({"days":bounded_days,"raw_tool_output_stored":False,"hidden_reasoning_imported":False}); return totals


def _loop()->None:
    while not _STOP.wait(1.5):
        try: scan_once()
        except Exception: pass


def start()->None:
    global _THREAD
    init_store()
    if _THREAD is not None and _THREAD.is_alive(): return
    _STOP.clear(); _THREAD=threading.Thread(target=_loop,name="owg-agent-working-detail",daemon=True); _THREAD.start()


def stop()->None: _STOP.set()


def status()->dict[str,Any]:
    state=_load_state(); return {"policy":read_policy(),"running":bool(_THREAD and _THREAD.is_alive()),"last_scan_at":state.get("last_scan_at"),"raw_tool_output_stored":False,"hidden_reasoning_captured":False,"absolute_workspace_paths_stored":False}


__all__=["capture_claude_hook","cleanup","delete_all","details_for_session","import_recent","init_store","read_policy","repository_state","scan_once","start","status","stop","write_policy"]
