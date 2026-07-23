import pytest


class FakeConnection:
    def __init__(self):
        self.calls = []
        self.closed = False

    def execute(self, clause, params=None):
        self.calls.append((str(clause), params))

    def close(self):
        self.closed = True


class FakeEngine:
    def __init__(self, dialect_name):
        self.dialect = type('Dialect', (), {'name': dialect_name})()
        self.connect_count = 0
        self.last_connection = None

    def connect(self):
        self.connect_count += 1
        self.last_connection = FakeConnection()
        return self.last_connection


def test_schema_setup_lock_is_noop_for_non_postgres():
    from app.db_bootstrap import schema_setup_lock

    engine = FakeEngine('sqlite')
    entered = False
    with schema_setup_lock(engine):
        entered = True
    assert entered
    assert engine.connect_count == 0


def test_schema_setup_lock_acquires_and_releases_on_postgres():
    from app.db_bootstrap import schema_setup_lock

    engine = FakeEngine('postgresql')
    with schema_setup_lock(engine):
        pass
    conn = engine.last_connection
    assert conn.closed is True
    assert 'pg_advisory_lock' in conn.calls[0][0]
    assert 'pg_advisory_unlock' in conn.calls[1][0]


def test_schema_setup_lock_releases_even_on_exception():
    from app.db_bootstrap import schema_setup_lock

    engine = FakeEngine('postgresql')
    with pytest.raises(RuntimeError):
        with schema_setup_lock(engine):
            raise RuntimeError('boom')
    conn = engine.last_connection
    assert conn.closed is True
    assert 'pg_advisory_unlock' in conn.calls[-1][0]
