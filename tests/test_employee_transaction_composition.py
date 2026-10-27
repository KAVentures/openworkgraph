"""Roster state, team membership and offboarding compose in one transaction."""
import pytest
from gateway import employees as roster
from gateway.human_access import init_human_access_schema,actor_teams
from test_process_collaboration import store


def setup(db):
    roster.init_employee_schema(db);init_human_access_schema(db)


def test_team_write_failure_rolls_back_employee_creation(store,monkeypatch):
    db=store.db;setup(db);execute=db._execute
    def fail(conn,sql,params=()):
        if 'INSERT INTO gateway_actor_teams' in sql:raise RuntimeError('Synthetic team write failure')
        return execute(conn,sql,params)
    monkeypatch.setattr(db,'_execute',fail)
    with pytest.raises(RuntimeError):roster.upsert_employee(db,'acme',email='new@acme.test',teams=['sales'])
    assert roster.list_employees(db,'acme',include_offboarded=True)==[]


def test_team_clear_failure_rolls_back_employee_offboarding_and_epoch(store,monkeypatch):
    db=store.db;setup(db)
    e,_=roster.upsert_employee(db,'acme',email='person@acme.test',teams=['sales'])
    execute=db._execute
    def fail(conn,sql,params=()):
        if 'DELETE FROM gateway_actor_teams' in sql:raise RuntimeError('Synthetic team clear failure')
        return execute(conn,sql,params)
    monkeypatch.setattr(db,'_execute',fail)
    with pytest.raises(RuntimeError):roster.offboard_employee(db,'acme',e['employee_id'])
    assert roster.get_employee(db,'acme',e['employee_id'])['status']=='active'
    assert actor_teams(db,'acme',e['actor_id'])=={'sales'}
    with db.connect() as c:
        assert not roster._one(db,c,'SELECT * FROM gateway_employee_access_epochs WHERE employee_id=?',(e['employee_id'],))
