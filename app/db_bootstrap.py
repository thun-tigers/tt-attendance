"""Serialisiert das Schema-Bootstrap beim App-Start ueber parallele Gunicorn-Worker.

Mehrere Worker rufen beim Start unabhaengig voneinander create_app() und damit
db.create_all() (plus Schema-Shims/Seed-Defaults) auf. Ohne Sperre koennen sie
auf Postgres gegeneinander racen: metadata.create_all() prueft per Reflection,
ob eine Tabelle/Sequence schon existiert, das ist aber nicht transaktionssicher
gegen ein paralleles CREATE eines anderen Workers (UniqueViolation auf den
System-Katalogen wie pg_class/pg_type).
"""

from contextlib import contextmanager

from sqlalchemy import text

_SCHEMA_SETUP_LOCK_KEY = 92742001


@contextmanager
def schema_setup_lock(engine):
    """Serialisiert den umschlossenen Block ueber alle Worker/Prozesse hinweg.

    Auf Postgres per session-gebundenem Advisory-Lock (blockierend, wird beim
    Schliessen der Verbindung automatisch freigegeben). Fuer andere Dialekte
    (SQLite in Tests) ist die Sperre ein No-Op, da dort ohnehin nur ein
    Prozess pro Datei sinnvoll ist.
    """
    if engine.dialect.name != "postgresql":
        yield
        return

    conn = engine.connect()
    try:
        conn.execute(text("SELECT pg_advisory_lock(:key)"), {"key": _SCHEMA_SETUP_LOCK_KEY})
        yield
    finally:
        conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _SCHEMA_SETUP_LOCK_KEY})
        conn.close()
