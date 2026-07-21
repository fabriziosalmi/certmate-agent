"""A fresh install must be able to create its database.

Regression test for a break introduced while removing the pending-actions
table: the slice that took out `pending_actions` also took out the
`CREATE TABLE audit_log` above it, leaving an index on a table that no longer
existed. `init_db()` raised `sqlite3.OperationalError: no such table:
main.audit_log` — every fresh install would have failed at boot, while every
existing one kept working because the table was already there.

The whole test suite passed. Nothing exercised init_db() against an empty
file, which is exactly the state a new user starts from.
"""


import pytest


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """Point the module at a database file that does not exist yet."""
    import agent.db as db

    monkeypatch.setattr(db.settings, "agent_db_path", str(tmp_path / "agent.db"))
    monkeypatch.setattr(db, "_conn_cache", None, raising=False)
    return db


def test_init_db_succeeds_on_an_empty_directory(fresh_db):
    fresh_db.init_db()  # must not raise


def test_every_table_the_code_writes_to_exists(fresh_db):
    fresh_db.init_db()
    conn = fresh_db._conn()
    names = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }

    assert "audit_log" in names
    assert "conversation_messages" in names
    # And the surface that was removed is really gone.
    assert "pending_actions" not in names


def test_the_audit_trail_is_writable_and_readable(fresh_db):
    fresh_db.init_db()

    fresh_db.audit("turn", "ok", detail="hello")

    # sqlite3.Row, not a tuple — the connection sets a row factory.
    rows = [
        tuple(r)
        for r in fresh_db._conn().execute(
            "SELECT kind, status, detail FROM audit_log"
        )
    ]
    assert rows == [("turn", "ok", "hello")]


def test_init_db_is_idempotent(fresh_db):
    fresh_db.init_db()
    fresh_db.audit("turn", "ok")
    fresh_db.init_db()  # a restart must not wipe or fail

    count = fresh_db._conn().execute("SELECT count(*) FROM audit_log").fetchone()[0]
    assert count == 1


def test_pruning_works_on_a_fresh_database(fresh_db):
    """The scheduler runs a pass on boot; it must not fault on an empty db."""
    fresh_db.init_db()
    assert fresh_db.audit_prune_older_than(90) == 0
    assert fresh_db.conversation_prune_older_than(30) == 0
