from concurrent.futures import ThreadPoolExecutor

import pytest

from server import db
from server import local_process_dependencies as dependencies
from server.local_process_catalog import catalog
from shared.process_catalog import CatalogWrite


@pytest.fixture(autouse=True)
def isolated_catalog(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "catalog.sqlite")


def design(title, previous=None):
    return catalog.save(CatalogWrite(title=title, user_confirmed=True,
        workflow_id=previous["workflow_id"] if previous else None,
        expected_revision=previous["revision"] if previous else 0, process_model={
            "goal": "Review the authorized case", "baseline_source": "user_declared",
            "baseline_path": ["Review"], "activities": [{"id": "review", "label": "Review"}],
            "entry_activity_id": "review", "terminal_activity_ids": ["review"],
        }))


def declare(root, prerequisite, expected=0, *, confirmed=True):
    links = [] if prerequisite is None else [
        {"id": "output", "prerequisite": dependencies._pin(prerequisite),
         "relationship": "uses_output", "reason": "The next work uses this reviewed output"}
    ]
    draft = dependencies.SaveDependencies(root_process=dependencies._pin(root), links=links,
                                          expected_revision=expected, user_confirmed=confirmed)
    return dependencies.save(root["workflow_id"], draft)


def test_exact_pins_change_and_deletion_keeps_consumer_history():
    start, middle, final = [design(title) for title in ("Start", "Middle", "Final")]
    with pytest.raises(dependencies.DependencyConflict, match="confirm"):
        declare(middle, start, confirmed=False)
    assert declare(middle, start)["links"][0]["resolution"]["state"] == "current"
    declare(final, middle)
    root = dependencies.detail(start["workflow_id"])
    assert [(row["process"]["workflow_id"], row["distance"]) for row in root["potential_consumers"]] == [
        (middle["workflow_id"], 1), (final["workflow_id"], 2)
    ]
    latest = design("Start updated", start)
    root = dependencies.detail(start["workflow_id"])
    direct = root["potential_consumers"][0]
    assert direct["direct_prerequisite_revision"] == 1 and not direct["direct_prerequisite_current"]
    old = dependencies.detail(middle["workflow_id"])["links"][0]["resolution"]
    assert old["state"] == "superseded" and old["pinned"]["revision"] == 1 and old["latest"]["revision"] == 2
    with pytest.raises(dependencies.DependencyConflict, match="changed or is unavailable"):
        declare(middle, start, expected=1)
    with pytest.raises(dependencies.DependencyConflict, match="declarations changed"):
        declare(middle, latest)
    assert declare(middle, latest, expected=1)["declaration"]["revision"] == 2
    assert dependencies.forget_process(start["workflow_id"])["versions_deleted"] == 2
    assert dependencies.detail(middle["workflow_id"])["links"][0]["resolution"]["state"] == "unavailable"
    assert dependencies.forget_process(middle["workflow_id"])["versions_deleted"] == 1
    with pytest.raises(LookupError):
        dependencies.detail(middle["workflow_id"])
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM workflow_knowledge WHERE owner=? AND workflow_id=?",
                            (dependencies.OWNER, middle["workflow_id"])).fetchone()[0] == 0


def test_concurrent_opposing_declarations_cannot_create_a_cycle():
    first, second = design("First"), design("Second")

    def attempt(root, prerequisite):
        try:
            declare(root, prerequisite)
        except ValueError as exc:
            assert "cycle" in str(exc)
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as pool:
        successes = [pool.submit(attempt, first, second), pool.submit(attempt, second, first)]
        assert sorted(future.result() for future in successes) == [False, True]
    assert sum(bool(dependencies.detail(item["workflow_id"])["declaration"]) for item in (first, second)) == 1
