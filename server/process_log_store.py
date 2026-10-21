"""Explicitly retained local event-log snapshots, separate from observed evidence.

Native case IDs and source values are discarded before storage. Snapshots never
join to observation, knowledge, synchronization or AI context automatically.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import csv
from io import StringIO
import json
from uuid import uuid4

from . import db
from .process_mining_csv import mine_csv_events, parse_csv_events

MAX_DATASETS = 20


class ProcessLogError(ValueError):
    pass


class ProcessLogStore:
    def __init__(self, connect=None, *, now=None):
        self.connect = connect or db.connect
        self.now = now or (lambda: datetime.now(timezone.utc))

    def _open(self, conn):
        conn.execute('''CREATE TABLE IF NOT EXISTS process_log_datasets (
            dataset_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
            case_count INTEGER NOT NULL, event_count INTEGER NOT NULL, report_json TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS process_log_cases (
            dataset_id TEXT NOT NULL, case_number INTEGER NOT NULL, variant_id TEXT NOT NULL,
            event_count INTEGER NOT NULL, first_event_at TEXT NOT NULL, last_event_at TEXT NOT NULL,
            duration_seconds REAL NOT NULL, tied_timestamp_adjacent_pairs INTEGER NOT NULL,
            PRIMARY KEY(dataset_id, case_number))''')
        conn.execute('''CREATE TABLE IF NOT EXISTS process_log_events (
            dataset_id TEXT NOT NULL, case_number INTEGER NOT NULL, position INTEGER NOT NULL,
            activity TEXT NOT NULL, observed_at TEXT NOT NULL, elapsed_since_previous_seconds REAL,
            PRIMARY KEY(dataset_id, case_number, position))''')
        conn.execute('''CREATE INDEX IF NOT EXISTS idx_process_log_activity
            ON process_log_events(dataset_id, activity, case_number)''')
        expired = conn.execute('SELECT dataset_id FROM process_log_datasets WHERE expires_at <= ?',
                               (self.now().isoformat(),)).fetchall()
        for row in expired:
            self._delete(conn, row[0])

    @staticmethod
    def _delete(conn, dataset_id):
        # All three deletes share the caller's transaction; no orphaned events.
        conn.execute('DELETE FROM process_log_events WHERE dataset_id = ?', (dataset_id,))
        conn.execute('DELETE FROM process_log_cases WHERE dataset_id = ?', (dataset_id,))
        conn.execute('DELETE FROM process_log_datasets WHERE dataset_id = ?', (dataset_id,))

    @staticmethod
    def _dataset(conn, dataset_id):
        row = conn.execute('''SELECT dataset_id, created_at, expires_at, case_count, event_count
            FROM process_log_datasets WHERE dataset_id = ?''', (dataset_id,)).fetchone()
        if not row:
            raise LookupError('Retained process log not found or expired')
        return dict(zip(('dataset_id','created_at','expires_at','case_count','event_count'),row))

    def save(self, raw, *, retention_days, confirmed):
        if confirmed is not True:
            raise ProcessLogError('Explicit local event-log retention confirmation is required')
        if type(retention_days) is not int or not 1 <= retention_days <= 30:
            raise ProcessLogError('Choose a retention period from 1 to 30 days')
        rows = parse_csv_events(raw)
        report = mine_csv_events(raw)
        groups = defaultdict(list)
        for native_id, activity, stamp, supplied_order in rows:
            groups[native_id].append((activity,stamp,supplied_order))
        ordered = [values for _,values in sorted(groups.items(),
            key=lambda item:(min(r[1] for r in item[1]),item[0]))]
        for values in ordered:
            values.sort(key=lambda row:(row[1],row[2]))
        paths = Counter(tuple(row[0] for row in values) for values in ordered)
        variants = {path:f'variant_{i}' for i,(path,_) in enumerate(paths.most_common(),1)}
        identifier = 'log_' + uuid4().hex
        created = self.now()
        expires = created + timedelta(days=retention_days)
        with self.connect() as conn:
            self._open(conn)
            # Serialize quota check with inserts on SQLite, including concurrent saves.
            if not conn.in_transaction:
                conn.execute('BEGIN IMMEDIATE')
            count = conn.execute('SELECT COUNT(*) FROM process_log_datasets').fetchone()[0]
            if count >= MAX_DATASETS:
                raise ProcessLogError('Local log limit reached; delete an existing snapshot before saving another')
            report.update(import_retained=True, dataset_id=identifier,
                          retention_scope='local_explicit_snapshot', expires_at=expires.isoformat())
            conn.execute('INSERT INTO process_log_datasets VALUES (?, ?, ?, ?, ?, ?)',
                (identifier,created.isoformat(),expires.isoformat(),len(ordered),len(rows),json.dumps(report)))
            for number, values in enumerate(ordered,1):
                path = tuple(row[0] for row in values)
                ties = sum(a[1]==b[1] for a,b in zip(values,values[1:]))
                conn.execute('INSERT INTO process_log_cases VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                    (identifier,number,variants[path],len(values),values[0][1].isoformat(),values[-1][1].isoformat(),
                     round((values[-1][1]-values[0][1]).total_seconds(),3),ties))
                conn.executemany('INSERT INTO process_log_events VALUES (?, ?, ?, ?, ?, ?)', [
                    (identifier,number,i+1,activity,stamp.isoformat(),
                     round((stamp-values[i-1][1]).total_seconds(),3) if i else None)
                    for i,(activity,stamp,_) in enumerate(values)])
            return self._dataset(conn,identifier) | {'native_case_ids_retained':False,
                'source_values_retained':False,'synchronized':False,'ai_access_enabled':False}

    def prune(self):
        with self.connect() as conn:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'process_log_datasets'").fetchone():
                return 0
            before=conn.execute('SELECT COUNT(*) FROM process_log_datasets').fetchone()[0]
            self._open(conn)
            return before-conn.execute('SELECT COUNT(*) FROM process_log_datasets').fetchone()[0]

    def inventory(self):
        with self.connect() as conn:
            self._open(conn)
            rows=conn.execute('SELECT dataset_id FROM process_log_datasets ORDER BY created_at DESC, dataset_id').fetchall()
            return {'datasets':[self._dataset(conn,row[0]) for row in rows], 'limit':MAX_DATASETS,
                    'dashboard_only':True, 'synchronized':False}

    def report(self, dataset_id, *, reference_model=None, reference=None):
        with self.connect() as conn:
            self._open(conn)
            dataset=self._dataset(conn,dataset_id)
            if reference_model is None:
                return json.loads(conn.execute('SELECT report_json FROM process_log_datasets WHERE dataset_id = ?',
                                               (dataset_id,)).fetchone()[0])
            # Compare every full retained path using the same bounded engine as
            # a fresh CSV. Dataset ordinals never recover discarded source keys.
            rows=conn.execute('''SELECT case_number, activity, observed_at FROM process_log_events
                WHERE dataset_id = ? ORDER BY case_number, position''',(dataset_id,)).fetchall()
        stream=StringIO(newline='');writer=csv.writer(stream)
        writer.writerow(('case_id','activity','timestamp'))
        writer.writerows((f'{number:04d}',activity,stamp) for number,activity,stamp in rows)
        report=mine_csv_events(stream.getvalue().encode('utf-8'),reference_model=reference_model)
        report.update(import_retained=True,dataset_id=dataset_id,
                      retention_scope='local_explicit_snapshot',expires_at=dataset['expires_at'])
        report['conformance']['reference']=reference
        # Re-analysis sees the already sorted snapshot; preserve original input
        # diagnostics, which cannot be reconstructed after source keys are gone.
        with self.connect() as conn:
            self._open(conn);self._dataset(conn,dataset_id)
            original=json.loads(conn.execute('SELECT report_json FROM process_log_datasets WHERE dataset_id = ?',
                                           (dataset_id,)).fetchone()[0])
        report['data_quality']=original['data_quality']
        return report

    def cases(self, dataset_id, *, after=0, limit=25, activity=None, variant_id=None):
        with self.connect() as conn:
            self._open(conn)
            dataset=self._dataset(conn,dataset_id)
            sql='SELECT c.* FROM process_log_cases c WHERE c.dataset_id = ? AND c.case_number > ?'
            params=[dataset_id,after]
            if activity is not None:
                sql+=' AND EXISTS (SELECT 1 FROM process_log_events e WHERE e.dataset_id = c.dataset_id AND e.case_number = c.case_number AND e.activity = ?)'
                params.append(activity)
            if variant_id is not None:
                sql+=' AND c.variant_id = ?';params.append(variant_id)
            cap=max(1,min(int(limit),100))
            rows=conn.execute(sql+' ORDER BY c.case_number LIMIT ?',params+[cap+1]).fetchall()
            keys=('dataset_id','case_number','variant_id','event_count','first_event_at','last_event_at',
                  'duration_seconds','tied_timestamp_adjacent_pairs')
            items=[dict(zip(keys,row)) for row in rows[:cap]]
            return {'dataset':dataset,'cases':items,'has_more':len(rows)>cap,
                    'next_after':items[-1]['case_number'] if len(rows)>cap else None,
                    'case_ids_are_dataset_ordinals':True,'case_end_is_business_completion':False}

    def events(self, dataset_id, case_number, *, after=0, limit=100):
        with self.connect() as conn:
            self._open(conn)
            self._dataset(conn,dataset_id)
            if not conn.execute('SELECT 1 FROM process_log_cases WHERE dataset_id = ? AND case_number = ?',
                                (dataset_id,case_number)).fetchone():
                raise LookupError('Retained case not found')
            cap=max(1,min(int(limit),100))
            rows=conn.execute('''SELECT position, activity, observed_at, elapsed_since_previous_seconds
                FROM process_log_events WHERE dataset_id = ? AND case_number = ? AND position > ?
                ORDER BY position LIMIT ?''',(dataset_id,case_number,after,cap+1)).fetchall()
            keys=('position','activity','observed_at','elapsed_since_previous_seconds')
            items=[dict(zip(keys,row)) for row in rows[:cap]]
            return {'dataset_id':dataset_id,'case_number':case_number,'events':items,
                    'has_more':len(rows)>cap,'next_after':items[-1]['position'] if len(rows)>cap else None,
                    'ordering_basis':'timestamp_then_supplied_row_order','tied_timestamp_order_verified':False,
                    'gaps_are_verified_waiting':False}

    def forget(self, dataset_id, *, confirmed):
        if confirmed is not True:
            raise ProcessLogError('Explicit snapshot deletion confirmation is required')
        with self.connect() as conn:
            self._open(conn)
            dataset=self._dataset(conn,dataset_id)
            self._delete(conn,dataset_id)
            return {'deleted':True,**dataset,'observed_evidence_deleted':False}
