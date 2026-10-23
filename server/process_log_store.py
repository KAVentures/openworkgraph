"""Explicitly retained local event-log snapshots, separate from observed evidence.

Native case IDs and source values are discarded before storage. Snapshots never
join to observation, knowledge, synchronization or AI context automatically.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import csv
from io import StringIO
from hashlib import sha256
import json
import re
from uuid import uuid4

from . import db
from .process_mining_csv import mine_csv_events, parse_csv_event_records, PARTICIPANT_DIMENSIONS
from shared.process_drift import compare_snapshots

MAX_DATASETS = 20


class ProcessLogError(ValueError):
    pass


def _case_cohort(*, started_since=None, started_before=None, contains_activity=None, variant_id=None, contains_participant=None, contains_team=None):
    """Select whole cases. Never clip events or infer business start/completion."""
    filters = {}
    for key, value in (('started_since', started_since), ('started_before', started_before)):
        if value is None:
            continue
        try:
            if not isinstance(value, str) or not 1 <= len(value) <= 64:
                raise ValueError()
            stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if stamp.tzinfo is None or stamp.utcoffset() is None:
                raise ValueError()
            filters[key] = stamp.astimezone(timezone.utc).isoformat()
        except (ValueError, OverflowError):
            raise ProcessLogError('Cohort dates must be offset-aware ISO-8601 timestamps') from None
    if ('started_since' in filters and 'started_before' in filters and
        filters['started_since'] >= filters['started_before']):
        raise ProcessLogError('Cohort start must be earlier than its exclusive end')
    for key, value in (('contains_activity', contains_activity), ('variant_id', variant_id)):
        if value is not None:
            if not isinstance(value, str) or not 1 <= len(value) <= 64:
                raise ProcessLogError('Cohort activity and variant filters must be short nonempty labels')
            filters[key] = value
    for key,value in (('participant',contains_participant),('team',contains_team)):
        if value is not None:
            if (not isinstance(value,str) or not re.fullmatch(key+r'_[1-9][0-9]{0,3}',value)
                or int(value.rsplit('_',1)[1])>1000):
                raise ProcessLogError('Participant/team filters must use bounded labels from this snapshot')
            filters['contains_'+key]=value
    sql, params = [], []
    for key, operator in (('started_since', '>='), ('started_before', '<')):
        if key in filters:
            sql.append('c.first_event_at ' + operator + ' ?');params.append(filters[key])
    if 'contains_activity' in filters:
        sql.append('EXISTS (SELECT 1 FROM process_log_events ce WHERE ce.dataset_id = c.dataset_id '
                   'AND ce.case_number = c.case_number AND ce.activity = ?)')
        params.append(filters['contains_activity'])
    if 'variant_id' in filters:
        sql.append('c.variant_id = ?');params.append(filters['variant_id'])
    selected=[key for key in PARTICIPANT_DIMENSIONS if 'contains_'+key in filters]
    if selected:
        sql.append('EXISTS (SELECT 1 FROM process_log_events pe WHERE pe.dataset_id = c.dataset_id '
            'AND pe.case_number = c.case_number'+''.join(' AND pe.'+key+' = ?' for key in selected)+')')
        params.extend(filters['contains_'+key] for key in selected)
    return filters, ''.join(' AND ' + condition for condition in sql), params


def _label_dimensions(report):
    return [key for key in PARTICIPANT_DIMENSIONS if key in report.get('participant_labels',{}).get('dimensions',[])]


def _snapshot_csv(rows,dimensions,correlation=False):
    stream=StringIO(newline='');writer=csv.writer(stream)
    writer.writerow(('case_id','activity','timestamp',*dimensions,*(['origin'] if correlation else [])))
    for row in rows:
        if correlation and not row[6]:raise ProcessLogError('Retained shared-key provenance is incomplete')
        writer.writerow((row[6] if correlation else f'{row[0]:04d}',row[1],row[2],
            *(row[3 if key=='participant' else 4] or '' for key in dimensions),
            *([row[5]] if correlation else [])))
    return stream.getvalue().encode('utf-8')


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
            duration_seconds REAL NOT NULL, tied_timestamp_adjacent_pairs INTEGER NOT NULL, case_key_alias TEXT,
            PRIMARY KEY(dataset_id, case_number))''')
        conn.execute('''CREATE TABLE IF NOT EXISTS process_log_events (
            dataset_id TEXT NOT NULL, case_number INTEGER NOT NULL, position INTEGER NOT NULL,
            activity TEXT NOT NULL, observed_at TEXT NOT NULL, elapsed_since_previous_seconds REAL,
            participant TEXT, team TEXT, origin TEXT,
            PRIMARY KEY(dataset_id, case_number, position))''')
        conn.execute('''CREATE INDEX IF NOT EXISTS idx_process_log_activity
            ON process_log_events(dataset_id, activity, case_number)''')
        columns={row[1] for row in conn.execute('PRAGMA table_info(process_log_events)')}
        if not set((*PARTICIPANT_DIMENSIONS,'origin')).issubset(columns):
            if not conn.in_transaction:conn.execute('BEGIN IMMEDIATE')
            columns={row[1] for row in conn.execute('PRAGMA table_info(process_log_events)')}
            for key in (*PARTICIPANT_DIMENSIONS,'origin'):
                if key not in columns:conn.execute('ALTER TABLE process_log_events ADD COLUMN '+key+' TEXT')
        case_columns={row[1] for row in conn.execute('PRAGMA table_info(process_log_cases)')}
        if 'case_key_alias' not in case_columns:
            if not conn.in_transaction:conn.execute('BEGIN IMMEDIATE')
            case_columns={row[1] for row in conn.execute('PRAGMA table_info(process_log_cases)')}
            if 'case_key_alias' not in case_columns:conn.execute('ALTER TABLE process_log_cases ADD COLUMN case_key_alias TEXT')
        for key in PARTICIPANT_DIMENSIONS:
            conn.execute('CREATE INDEX IF NOT EXISTS idx_process_log_'+key+' ON process_log_events(dataset_id,'+key+',case_number) WHERE '+key+' IS NOT NULL')
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

    def save(self, raw, *, retention_days, confirmed, participant_labels_authorized=False, case_links_authorized=False):
        if confirmed is not True:
            raise ProcessLogError('Explicit local event-log retention confirmation is required')
        if type(retention_days) is not int or not 1 <= retention_days <= 30:
            raise ProcessLogError('Choose a retention period from 1 to 30 days')
        rows,dimensions = parse_csv_event_records(raw,participant_labels_authorized=participant_labels_authorized,case_links_authorized=case_links_authorized)
        report = mine_csv_events(raw,participant_labels_authorized=participant_labels_authorized,case_links_authorized=case_links_authorized)
        groups = defaultdict(list)
        for native_id, activity, stamp, supplied_order, participant, team, *origins in rows:
            groups[native_id].append((activity,stamp,supplied_order,participant,team,origins[0] if origins else None))
        ordered_items = sorted(groups.items(),key=lambda item:(min(r[1] for r in item[1]),item[0]))
        ordered = [values for _,values in ordered_items]
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
                conn.execute('INSERT INTO process_log_cases (dataset_id,case_number,variant_id,event_count,first_event_at,last_event_at,duration_seconds,tied_timestamp_adjacent_pairs,case_key_alias) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                    (identifier,number,variants[path],len(values),values[0][1].isoformat(),values[-1][1].isoformat(),
                     round((values[-1][1]-values[0][1]).total_seconds(),3),ties,ordered_items[number-1][0] if case_links_authorized is True else None))
                conn.executemany('INSERT INTO process_log_events (dataset_id,case_number,position,activity,observed_at,elapsed_since_previous_seconds,participant,team,origin) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', [
                    (identifier,number,i+1,activity,stamp.isoformat(),
                     round((stamp-values[i-1][1]).total_seconds(),3) if i else None,participant,team,origin)
                    for i,(activity,stamp,_,participant,team,origin) in enumerate(values)])
            return self._dataset(conn,identifier) | {'native_case_ids_retained':False,
                'source_values_retained':False,'synchronized':False,'ai_access_enabled':False,
                'native_participant_identifiers_retained':False,'participant_label_dimensions':list(dimensions),
                **({'case_key_aliases_retained':True,'source_origin_aliases_retained':True} if case_links_authorized is True else {})}

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
            if not conn.in_transaction:conn.execute('BEGIN')
            dataset=self._dataset(conn,dataset_id)
            original=json.loads(conn.execute('SELECT report_json FROM process_log_datasets WHERE dataset_id = ?',
                                             (dataset_id,)).fetchone()[0])
            if reference_model is None:
                return original
            # Compare every full retained path using the same bounded engine as
            # a fresh CSV. Dataset ordinals never recover discarded source keys.
            rows=conn.execute('''SELECT e.case_number, e.activity, e.observed_at, e.participant, e.team, e.origin, c.case_key_alias FROM process_log_events e
                JOIN process_log_cases c ON c.dataset_id = e.dataset_id AND c.case_number = e.case_number
                WHERE e.dataset_id = ? ORDER BY e.case_number, e.position''',(dataset_id,)).fetchall()
        dimensions=_label_dimensions(original);correlation=bool(original.get('case_correlation'))
        report=mine_csv_events(_snapshot_csv(rows,dimensions,correlation),reference_model=reference_model,
            participant_labels_authorized=bool(dimensions),case_links_authorized=correlation)
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

    def cases(self, dataset_id, *, after=0, limit=25, activity=None, variant_id=None,
              started_since=None, started_before=None, contains_activity=None, contains_participant=None, contains_team=None,
              transition_from=None, transition_to=None):
        if (transition_from is None) != (transition_to is None):
            raise ProcessLogError("Transition filtering requires both exact activity labels")
        if transition_from is not None and any(not isinstance(value,str) or not 1 <= len(value) <= 64
                                             for value in (transition_from,transition_to)):
            raise ProcessLogError("Transition activity labels must be short and nonempty")
        filters, cohort_sql, cohort_params = _case_cohort(started_since=started_since,
            started_before=started_before, contains_activity=contains_activity, variant_id=variant_id,
            contains_participant=contains_participant,contains_team=contains_team)
        with self.connect() as conn:
            self._open(conn)
            dataset=self._dataset(conn,dataset_id)
            sql=('SELECT c.dataset_id,c.case_number,c.variant_id,c.event_count,c.first_event_at,c.last_event_at,'
                 'c.duration_seconds,c.tied_timestamp_adjacent_pairs,c.case_key_alias FROM process_log_cases c '
                 'WHERE c.dataset_id = ? AND c.case_number > ?')
            params=[dataset_id,after]
            if activity is not None:
                sql+=' AND EXISTS (SELECT 1 FROM process_log_events e WHERE e.dataset_id = c.dataset_id AND e.case_number = c.case_number AND e.activity = ?)'
                params.append(activity)
            if transition_from is not None:
                sql += (''' AND EXISTS (SELECT 1 FROM process_log_events te
                    JOIN process_log_events tn ON tn.dataset_id = te.dataset_id
                      AND tn.case_number = te.case_number AND tn.position = te.position + 1
                    WHERE te.dataset_id = c.dataset_id AND te.case_number = c.case_number
                      AND te.activity = ? AND tn.activity = ?)''')
                params.extend((transition_from,transition_to))
            sql+=cohort_sql;params+=cohort_params
            cap=max(1,min(int(limit),100))
            rows=conn.execute(sql+' ORDER BY c.case_number LIMIT ?',params+[cap+1]).fetchall()
            keys=('dataset_id','case_number','variant_id','event_count','first_event_at','last_event_at',
                  'duration_seconds','tied_timestamp_adjacent_pairs')
            items=[dict(zip(keys,row)) | ({'case_key_alias':row[8]} if row[8] is not None else {}) for row in rows[:cap]]
            return {'dataset':dataset,'cases':items,'has_more':len(rows)>cap, 'cohort_filters':filters,
                    'next_after':items[-1]['case_number'] if len(rows)>cap else None,
                    'case_ids_are_dataset_ordinals':True,'case_end_is_business_completion':False,
                    **({'transition_filter':{'from':transition_from,'to':transition_to},
                        'transition_basis':'adjacent_retained_positions_timestamp_then_supplied_row_order',
                        'tied_timestamp_order_verified':False} if transition_from is not None else {})}

    def cohort(self, dataset_id, *, started_since=None, started_before=None, contains_activity=None,
               variant_id=None, reference_model=None, reference=None, contains_participant=None, contains_team=None):
        filters, predicate, params = _case_cohort(started_since=started_since,
            started_before=started_before, contains_activity=contains_activity, variant_id=variant_id,
            contains_participant=contains_participant,contains_team=contains_team)
        with self.connect() as conn:
            self._open(conn)
            if not conn.in_transaction:
                conn.execute('BEGIN')
            dataset = self._dataset(conn, dataset_id)
            original = json.loads(conn.execute('SELECT report_json FROM process_log_datasets WHERE dataset_id = ?',
                                               (dataset_id,)).fetchone()[0])
            selected = conn.execute('''SELECT c.case_number, c.variant_id FROM process_log_cases c
                WHERE c.dataset_id = ?''' + predicate + ' ORDER BY c.first_event_at, c.case_number',
                [dataset_id, *params]).fetchall()
            rows = conn.execute('''SELECT e.case_number, e.activity, e.observed_at, e.participant, e.team, e.origin, c.case_key_alias FROM process_log_events e
                JOIN process_log_cases c ON c.dataset_id = e.dataset_id AND c.case_number = e.case_number
                WHERE c.dataset_id = ?''' + predicate + ' ORDER BY c.first_event_at, c.case_number, e.position',
                [dataset_id, *params]).fetchall()
        selection = {'filters':filters, 'dataset_case_count':dataset['case_count'],
            'selected_case_count':len(selected), 'selected_event_count':len(rows),
            'date_basis':'first_retained_event_in_case_inclusive_since_exclusive_before',
            'all_events_of_selected_cases_included':True, 'events_clipped_to_time_window':False,
            'case_ordinals_and_variant_ids_preserved':True, 'business_start_or_completion_verified':False}
        envelope = {'format':'openworkgraph.process-log-cohort.v1', 'dataset':dataset,
                    'selection':selection, 'report':None, 'selection_retained':False}
        if not selected:
            return envelope
        dimensions=_label_dimensions(original);correlation=bool(original.get('case_correlation'))
        result=mine_csv_events(_snapshot_csv(rows,dimensions,correlation),reference_model=reference_model,
            participant_labels_authorized=bool(dimensions),case_links_authorized=correlation)
        # The mining engine returns per-analysis ordinals. Restore the immutable
        # snapshot's original case/variant identities before returning examples,
        # including variant examples outside the top-level 30-case display cap.
        paths=defaultdict(list)
        for number,activity,*_ in rows:
            paths[number].append(activity)
        stored_variants=dict(selected)
        variants=Counter(tuple(path) for path in paths.values())
        path_ids={tuple(path):stored_variants[number] for number,path in paths.items()}
        ordinal_variants={f'variant_{index}':path_ids[path]
                          for index,(path,_) in enumerate(variants.most_common(),1)}
        case_numbers=[row[0] for row in selected]
        seen=set()
        for item in result['cases']+[example for variant in result['variants'] for example in variant['case_examples']]:
            if id(item) in seen:
                continue
            seen.add(id(item))
            position=int(item['case'].split(' ')[1])-1
            item['case']='Case '+str(case_numbers[position])
            item['variant_id']=ordinal_variants[item['variant_id']]
        for variant in result['variants']:
            variant['variant_id']=ordinal_variants[variant['variant_id']]
        result.update(import_retained=True,dataset_id=dataset_id,expires_at=dataset['expires_at'],
            retention_scope='local_explicit_snapshot',cohort=selection,
            original_snapshot_data_quality=original['data_quality'])
        # Original pre-sort order cannot be recovered per selected case. The
        # whole-snapshot diagnostic is retained separately, never relabeled as
        # the selected population's count.
        result['data_quality']['cases_sorted_from_nonchronological_input']=None
        if reference_model is not None:
            result['conformance']['reference']=reference
        envelope['report']=result
        return envelope

    def compare(self, baseline_id, current_id, *, comparability_confirmed,
                reference_model=None, reference=None):
        if comparability_confirmed is not True:
            raise ProcessLogError('Confirm that both logs describe the same process with compatible activity labels')
        if baseline_id == current_id:
            raise ProcessLogError('Choose two different retained event-log snapshots')
        snapshots = []
        with self.connect() as conn:
            self._open(conn)
            # Pin existence, metadata and all event rows to one SQLite read
            # snapshot. Concurrent deletion cannot create a partial comparison.
            if not conn.in_transaction:
                conn.execute('BEGIN')
            for identifier in (baseline_id, current_id):
                dataset = self._dataset(conn, identifier)
                rows = conn.execute('''SELECT case_number, position, activity, observed_at,
                    elapsed_since_previous_seconds FROM process_log_events
                    WHERE dataset_id = ? ORDER BY case_number, position''', (identifier,)).fetchall()
                original = json.loads(conn.execute('''SELECT report_json FROM process_log_datasets
                    WHERE dataset_id = ?''', (identifier,)).fetchone()[0])
                groups = defaultdict(list)
                for number, position, activity, stamp, gap in rows:
                    groups[number].append((activity, stamp, gap))
                cases = [{
                    'path': tuple(event[0] for event in events),
                    'duration_seconds': round((datetime.fromisoformat(events[-1][1]) -
                                               datetime.fromisoformat(events[0][1])).total_seconds(), 3),
                    'gaps': [event[2] for event in events[1:]],
                } for events in groups.values()]
                snapshot_hash = sha256(json.dumps([tuple(row) for row in rows], ensure_ascii=False,
                    separators=(',', ':')).encode('utf-8')).hexdigest()
                snapshots.append({
                    'reference': dataset | {'events_sha256': snapshot_hash},
                    'cases': cases, 'data_quality': original['data_quality'],
                    'event_window': {'first_event_at': min(row[3] for row in rows),
                                     'last_event_at': max(row[3] for row in rows)},
                })
        return compare_snapshots(*snapshots, reference_model=reference_model, reference=reference)

    def compare_cohorts(self, dataset_id, *, baseline_filters, current_filters,
                        comparability_confirmed, reference_model=None, reference=None):
        """Compare complete populations from one immutable local snapshot."""
        if comparability_confirmed is not True:
            raise ProcessLogError('Review both complete-case selections before comparing')
        allowed = {'started_since', 'started_before', 'contains_activity', 'variant_id',
                   'contains_participant', 'contains_team'}
        selections = []
        for value in (baseline_filters, current_filters):
            if not isinstance(value, dict) or not set(value).issubset(allowed):
                raise ProcessLogError('Use only supported complete-case filters')
            selections.append(_case_cohort(**value))
        snapshots, selected_cases = [], []
        with self.connect() as conn:
            self._open(conn)
            if not conn.in_transaction:
                conn.execute('BEGIN')
            dataset = self._dataset(conn, dataset_id)
            for filters, predicate, params in selections:
                rows = conn.execute('''SELECT e.case_number, e.position, e.activity, e.observed_at,
                    e.elapsed_since_previous_seconds FROM process_log_events e
                    JOIN process_log_cases c ON c.dataset_id = e.dataset_id AND c.case_number = e.case_number
                    WHERE c.dataset_id = ?''' + predicate + ' ORDER BY e.case_number, e.position',
                    [dataset_id, *params]).fetchall()
                if not rows:
                    raise ProcessLogError('Both cohort selections must contain at least one complete case')
                groups = defaultdict(list)
                for number, position, activity, stamp, gap in rows:
                    groups[number].append((activity, stamp, gap))
                selected_cases.append(set(groups))
                cases = [{'path': tuple(event[0] for event in events),
                          'duration_seconds': round((datetime.fromisoformat(events[-1][1]) -
                                                     datetime.fromisoformat(events[0][1])).total_seconds(), 3),
                          'gaps': [event[2] for event in events[1:]]} for events in groups.values()]
                tied_cases = sum(any(a[1] == b[1] for a, b in zip(events, events[1:]))
                                 for events in groups.values())
                snapshots.append({
                    'reference': dataset | {'dataset_case_count': dataset['case_count'],
                        'case_count': len(groups), 'event_count': len(rows), 'cohort_filters': filters,
                        'events_sha256': sha256(json.dumps([tuple(row) for row in rows],
                            ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()},
                    'cases': cases,
                    'data_quality': {'cases_with_tied_timestamps': tied_cases,
                                     'cases_sorted_from_nonchronological_input': None},
                    'event_window': {'first_event_at': min(row[3] for row in rows),
                                     'last_event_at': max(row[3] for row in rows)}})
        result = compare_snapshots(*snapshots, reference_model=reference_model, reference=reference)
        a, b = selected_cases
        result['format'] = 'openworkgraph.process-cohort-comparison.v1'
        result['comparison_scope'] = 'two_complete_case_cohorts_in_one_retained_snapshot'
        result['cohort_overlap'] = {'case_identity_basis': 'same_snapshot_case_ordinal',
            'shared_case_count': len(a & b), 'baseline_only_case_count': len(a - b),
            'current_only_case_count': len(b - a), 'union_case_count': len(a | b),
            'cohorts_are_disjoint': not bool(a & b)}
        result['comparability'].update(cases_joined_across_snapshots=False,
            case_overlap_known_within_snapshot=True, statistical_independence_verified=False)
        result['warnings'] = [code for code in result['warnings'] if code != 'case_identity_not_joined']
        if a & b:
            result['warnings'].append('cohorts_share_retained_cases')
        result['coverage'].pop('all_retained_cases_and_events_used')
        result['coverage']['all_selected_complete_cases_and_events_used'] = True
        result['coverage']['all_events_of_selected_cases_included'] = True
        result['coverage']['events_clipped_to_filter_window'] = False
        if 'conformance' in result:
            result['conformance'].pop('all_retained_cases_and_full_paths_compared')
            result['conformance']['all_selected_cases_and_full_paths_compared'] = True
        return result

    def events(self, dataset_id, case_number, *, after=0, limit=100):
        with self.connect() as conn:
            self._open(conn)
            self._dataset(conn,dataset_id)
            if not conn.execute('SELECT 1 FROM process_log_cases WHERE dataset_id = ? AND case_number = ?',
                                (dataset_id,case_number)).fetchone():
                raise LookupError('Retained case not found')
            cap=max(1,min(int(limit),100))
            rows=conn.execute('''SELECT position, activity, observed_at, elapsed_since_previous_seconds, participant, team, origin
                FROM process_log_events WHERE dataset_id = ? AND case_number = ? AND position > ?
                ORDER BY position LIMIT ?''',(dataset_id,case_number,after,cap+1)).fetchall()
            keys=('position','activity','observed_at','elapsed_since_previous_seconds')
            items=[dict(zip(keys,row[:4])) | {key:row[4+index] for index,key in enumerate(PARTICIPANT_DIMENSIONS) if row[4+index] is not None} | ({'origin':row[6]} if row[6] is not None else {}) for row in rows[:cap]]
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
