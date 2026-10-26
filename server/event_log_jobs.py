"""Bounded, ephemeral, same-dashboard-session local analysis jobs.

No raw log copy, durable queue, credentials, or jobs surviving a process restart.
The existing workspace lock provides a coherent log at execution time, not a
pinned version at submission. A single daemon worker never runs arbitrary SQL.
"""
from collections import deque
from datetime import datetime, timezone
import hashlib
import json
import secrets
import threading
import time

from . import event_log_query as analysis
from . import event_log_workspace as workspace
from .event_log_execution import ExecutionControl, ExecutionStopped

MAX_PENDING = 4  # Includes the running job, globally (not per session).
MAX_RETAINED = 20
RETENTION_SECONDS = 600
DEADLINE_SECONDS = 120
MAX_RESULT_BYTES = 2 * 1024 * 1024
_TERMINAL = {'succeeded', 'cancelled', 'timed_out', 'failed'}


def session_key(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def _at():
    return datetime.now(timezone.utc).isoformat()


class JobError(ValueError):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


class AnalysisJobs:
    def __init__(self):
        self._condition = threading.Condition(threading.RLock())
        self._jobs = {}
        self._queue = deque()
        self._worker = None

    def _purge(self):
        now = time.monotonic()
        for job_id, job in list(self._jobs.items()):
            if job['state'] not in _TERMINAL and now >= job['control'].deadline:
                job['control'].stop('timed_out')
                job.update(state='timed_out', finished_at=_at(), result=None)
            if now >= job['expires']:
                job['control'].stop('timed_out')
                del self._jobs[job_id]
        # Remove dead queue references even if the worker is waiting for the workspace.
        self._queue = deque(i for i in self._queue if i in self._jobs)

    def submit(self, owner, log_id, spec):
        # Validate before allocating a queue slot. Copy the definition; callers cannot mutate it.
        parsed = analysis.parse(spec).model_dump(mode='json')
        if not workspace._LOG_ID.fullmatch(str(log_id)):
            raise JobError('Unknown event log', 404)
        definition = json.dumps(parsed, sort_keys=True, separators=(',', ':'), allow_nan=False)
        if len(definition.encode('utf-8')) > 128 * 1024:
            raise JobError('Analysis definition exceeds the job limit', 413)
        with self._condition:
            self._purge()
            if sum(j['state'] not in _TERMINAL for j in self._jobs.values()) >= MAX_PENDING:
                raise JobError('Local analysis queue is full; wait or cancel an existing job', 429)
            if len(self._jobs) >= MAX_RETAINED:
                raise JobError('Local job capacity reached; remove completed jobs or wait for expiry', 429)
            now = time.monotonic()
            job_id = 'job_' + secrets.token_hex(16)
            job = {'job_id': job_id, 'owner': owner, 'log_id': log_id, 'spec': parsed,
                   'spec_sha256': hashlib.sha256(definition.encode('utf-8')).hexdigest(),
                   'deadline_seconds': DEADLINE_SECONDS, 'retention_seconds': RETENTION_SECONDS,
                   'state': 'queued', 'submitted_at': _at(), 'started_at': None, 'finished_at': None,
                   'expires': now + RETENTION_SECONDS, 'control': ExecutionControl(now + DEADLINE_SECONDS),
                   'result': None, 'error': None}
            self._jobs[job_id] = job
            self._queue.append(job_id)
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._work, name='owg-local-analysis', daemon=True)
                self._worker.start()
            self._condition.notify_all()
            return self._status(job)

    @staticmethod
    def _status(job):
        return {k: job[k] for k in ('job_id', 'log_id', 'state', 'spec_sha256',
                                    'submitted_at', 'started_at', 'finished_at', 'error')} | {
            'result_available': job['state'] == 'succeeded',
            'retention_seconds': job['retention_seconds'], 'deadline_seconds': job['deadline_seconds'],
            'source_version_pinned_at_submission': False, 'durable': False,
            'execution_authorized': False}

    def _get(self, owner, log_id, job_id):
        self._purge()
        job = self._jobs.get(job_id)
        if not job or (job['owner'], job['log_id']) != (owner, log_id):
            raise JobError('Local analysis job not found or expired', 404)
        return job

    def status(self, owner, log_id, job_id):
        with self._condition:
            return self._status(self._get(owner, log_id, job_id))

    def result(self, owner, log_id, job_id):
        with self._condition:
            job = self._get(owner, log_id, job_id)
            if job['state'] != 'succeeded':
                raise JobError('No complete result is available for this job')
            # JSON bytes are immutable; no caller can modify a retained result.
            return job['result']

    def cancel(self, owner, log_id, job_id):
        with self._condition:
            job = self._get(owner, log_id, job_id)
            if job['state'] not in _TERMINAL:
                job['control'].stop()
                # Immediately withhold result publication, even if computation is finishing.
                job.update(state='cancelled', finished_at=_at(), result=None)
            self._condition.notify_all()
            return self._status(job)

    def remove(self, owner, log_id, job_id):
        with self._condition:
            job = self._get(owner, log_id, job_id)
            job['control'].stop()
            del self._jobs[job_id]
            self._purge()
            self._condition.notify_all()
            return {'deleted': True}

    def erase_log(self, log_id):
        with self._condition:
            for job_id, job in list(self._jobs.items()):
                if job['log_id'] == log_id:
                    job['control'].stop()
                    del self._jobs[job_id]
            self._purge()
            self._condition.notify_all()

    def _work(self):
        while True:
            with self._condition:
                self._purge()
                if not self._queue:
                    if self._jobs:
                        # Also erase expired results when nobody polls. No per-result timer threads.
                        self._condition.wait(timeout=max(0.01, min(j['expires'] for j in self._jobs.values()) - time.monotonic()))
                        continue
                    self._worker = None
                    return
                job = self._jobs.get(self._queue.popleft())
                if not job or job['state'] in _TERMINAL:
                    job = None
                    continue
                job.update(state='running', started_at=_at())
            control = job['control']
            timer = threading.Timer(max(0, control.deadline - time.monotonic()), control.stop, args=('timed_out',))
            timer.daemon = True
            timer.start()
            result = None
            try:
                control.check()
                value = analysis.run(job['log_id'], job['spec'], control=control)
                control.check()
                parts, size = [], 0
                for part in json.JSONEncoder(ensure_ascii=True, allow_nan=False, separators=(',', ':')).iterencode(value):
                    control.check()
                    encoded = part.encode('utf-8')
                    size += len(encoded)
                    if size > MAX_RESULT_BYTES:
                        raise JobError('Analysis result exceeds the local job byte budget; narrow the query')
                    parts.append(encoded)
                result = b''.join(parts)
                control.check()
                state, error = 'succeeded', None
            except ExecutionStopped as exc:
                state, error = exc.reason, None
            except workspace.EventLogWorkspaceError as exc:
                state, error = 'failed', str(exc)
            except JobError as exc:
                state, error = 'failed', str(exc)
            except Exception:
                # Driver exceptions may contain SQL or imported values; never expose them.
                state, error = 'failed', 'Analysis failed; no partial result was retained'
            finally:
                timer.cancel()
            with self._condition:
                # Cancel, delete, expiry and source erasure win over a late successful worker.
                if self._jobs.get(job['job_id']) is job and job['state'] == 'running':
                    try:
                        control.check()
                    except ExecutionStopped as exc:
                        state, error = exc.reason, None
                    job.update(state=state, error=error, finished_at=_at(),
                               result=result if state == 'succeeded' else None)
            # Do not keep query values or erased job objects alive in an idle worker frame.
            job = result = value = control = parts = encoded = part = timer = None


jobs = AnalysisJobs()
