"""
The suite's throwaway database is chosen once per run, however conftest is
imported.

pytest loads tests/conftest.py as the module ``conftest``. With ``tests`` and
``.`` both on pythonpath (pytest.ini), ``from tests.conftest import ...`` is a
DIFFERENT module name for the same file, so Python runs its top level a second
time: a fresh mkdtemp, and os.environ["DB_PATH"] pointed at the new, empty
file. app.db.DB_PATH was read once at import and stays on the first one. From
that moment every subprocess the suite starts, and every test that opened
os.environ["DB_PATH"] to probe for a lock, was looking at an empty file — and
passing. Three test files did that import; nothing noticed.

This test does the second import on purpose, so it fails whatever the
collection order, then checks the two paths still agree.
"""
import importlib
import os

from app import db


def test_importing_conftest_by_another_name_does_not_move_the_test_db():
    import conftest  # the copy pytest loaded

    importlib.import_module("tests.conftest")

    assert os.environ["DB_PATH"] == db.DB_PATH, (
        "conftest ran its setup twice: os.environ['DB_PATH'] now names a "
        "different file from the one the app is using"
    )
    assert os.environ["DB_PATH"] == conftest._TMP_DB
