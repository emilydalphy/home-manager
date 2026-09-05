"""
The admin scripts' production guard.

Both `reset_household.py` and `create_household.py` call
`misplaced_db_path_error()` before `init_db()`. The bug it exists to stop:
`railway run` injects the deployed service's variables and runs the
command on the laptop, where the volume DB_PATH points at is not mounted
-- so the script would create an empty database there and report success
against it, looking exactly like it had hit production.

Two layers of test, because the message alone is not the protection:
the pure-function tests below cover every branch of the decision, and the
subprocess tests at the bottom pin that the scripts actually STOP. An
earlier draft of this file had only the first kind, and a reviewer pointed
out that turning sys.exit into print would have kept all of them green
while destroying the entire point.
"""
import os
import subprocess
import sys

from app.db import misplaced_db_path_error, how_to_run_on_production

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _mounted(*paths):
    """Stand in for os.path.ismount: only the given paths are real mounts."""
    real = {os.path.abspath(p) for p in paths}
    return lambda p: os.path.abspath(p) in real


def _nothing_mounted(_path):
    return False


# ---------- the ordinary local case must stay silent ----------

def test_local_default_path_is_allowed(tmp_path):
    db = tmp_path / "home_manager.db"
    assert misplaced_db_path_error(str(db), env={}, is_mount=_nothing_mounted) is None


def test_relative_path_with_no_volume_is_allowed():
    """DB_PATH's default is a relative path inside the repo; don't refuse it."""
    assert misplaced_db_path_error(
        "app/home_manager.db", env={}, is_mount=_nothing_mounted
    ) is None


# ---------- the railway run mistake ----------

def test_volume_variable_set_but_nothing_mounted_is_refused(tmp_path):
    """
    THE case this guard exists for, and the one an adversarial review broke
    in the first draft: production's variables are present, /data happens to
    EXIST on this machine, and nothing is mounted there. Checking only that
    the directory existed let this through and created a decoy database.
    """
    mount = tmp_path / "data"
    mount.mkdir()  # exists, but is not a mount point
    db = mount / "home_manager.db"

    problem = misplaced_db_path_error(
        str(db),
        env={"RAILWAY_VOLUME_MOUNT_PATH": str(mount)},
        is_mount=_nothing_mounted,
    )
    assert problem is not None
    assert "nothing is mounted there" in problem
    assert "Nothing has been read or written." in problem


def test_volume_variable_set_and_directory_absent_is_refused(tmp_path):
    """The macOS shape of the same mistake: /data isn't there at all."""
    mount = tmp_path / "never-created"
    problem = misplaced_db_path_error(
        str(mount / "home_manager.db"),
        env={"RAILWAY_VOLUME_MOUNT_PATH": str(mount)},
        is_mount=_nothing_mounted,
    )
    assert problem is not None
    assert "nothing is mounted there" in problem


def test_db_path_injected_without_the_volume_variable_is_refused(tmp_path):
    """
    DB_PATH arrives but RAILWAY_VOLUME_MOUNT_PATH does not. Same laptop
    mistake; the missing parent directory is the only signal left.
    """
    missing = tmp_path / "not-mounted" / "home_manager.db"
    problem = misplaced_db_path_error(str(missing), env={}, is_mount=_nothing_mounted)
    assert problem is not None
    assert "does not exist" in problem
    assert str(tmp_path / "not-mounted") in problem


# ---------- inside the container ----------

def test_db_path_on_a_genuinely_mounted_volume_is_allowed(tmp_path):
    mount = tmp_path / "data"
    mount.mkdir()
    db = mount / "home_manager.db"
    assert misplaced_db_path_error(
        str(db),
        env={"RAILWAY_VOLUME_MOUNT_PATH": str(mount)},
        is_mount=_mounted(mount),
    ) is None


def test_trailing_slash_on_the_mount_variable_is_tolerated(tmp_path):
    mount = tmp_path / "data"
    mount.mkdir()
    assert misplaced_db_path_error(
        str(mount / "home_manager.db"),
        env={"RAILWAY_VOLUME_MOUNT_PATH": str(mount) + "/"},
        is_mount=_mounted(mount),
    ) is None


def test_db_path_outside_a_mounted_volume_is_refused(tmp_path):
    """
    The volume IS mounted, but DB_PATH points at a local file outside it.
    This one would succeed quietly against the wrong database, so it has to
    be caught too.
    """
    mount = tmp_path / "data"
    mount.mkdir()
    outside = tmp_path / "app" / "home_manager.db"
    outside.parent.mkdir()

    problem = misplaced_db_path_error(
        str(outside),
        env={"RAILWAY_VOLUME_MOUNT_PATH": str(mount)},
        is_mount=_mounted(mount),
    )
    assert problem is not None
    assert "outside it" in problem
    assert str(mount) in problem


def test_volume_prefix_match_is_not_fooled_by_a_sibling(tmp_path):
    """/data-old must not count as "inside" /data on a string prefix."""
    mount = tmp_path / "data"
    mount.mkdir()
    sibling = tmp_path / "data-old"
    sibling.mkdir()

    problem = misplaced_db_path_error(
        str(sibling / "home_manager.db"),
        env={"RAILWAY_VOLUME_MOUNT_PATH": str(mount)},
        is_mount=_mounted(mount),
    )
    assert problem is not None
    assert "outside it" in problem


# ---------- the escape hatch ----------

def test_documented_override_allows_a_refused_path(tmp_path):
    """
    The guard fails closed, so it needs a way through if the mount test ever
    misjudges the real container. It must be deliberate -- an exact phrase,
    not a truthy value.
    """
    mount = tmp_path / "data"
    mount.mkdir()
    env = {
        "RAILWAY_VOLUME_MOUNT_PATH": str(mount),
        "HOME_MANAGER_ADMIN_SKIP_PATH_CHECK": "i-am-in-the-container",
    }
    assert misplaced_db_path_error(
        str(mount / "home_manager.db"), env=env, is_mount=_nothing_mounted
    ) is None


def test_override_is_not_triggered_by_a_casual_truthy_value(tmp_path):
    mount = tmp_path / "data"
    mount.mkdir()
    for value in ("1", "true", "yes", ""):
        env = {
            "RAILWAY_VOLUME_MOUNT_PATH": str(mount),
            "HOME_MANAGER_ADMIN_SKIP_PATH_CHECK": value,
        }
        assert misplaced_db_path_error(
            str(mount / "home_manager.db"), env=env, is_mount=_nothing_mounted
        ) is not None, f"{value!r} must not disable the guard"


def test_refusal_tells_you_about_the_override(tmp_path):
    """A fail-closed guard has to explain how to get through it."""
    mount = tmp_path / "data"
    mount.mkdir()
    problem = misplaced_db_path_error(
        str(mount / "home_manager.db"),
        env={"RAILWAY_VOLUME_MOUNT_PATH": str(mount)},
        is_mount=_nothing_mounted,
    )
    assert "HOME_MANAGER_ADMIN_SKIP_PATH_CHECK=i-am-in-the-container" in problem


# ---------- the guidance text ----------

def test_guidance_names_the_script_the_operator_actually_ran():
    """
    A shared constant used to say create_household.py to everybody, so
    someone refused mid-reset was never told the reset command.
    """
    reset = how_to_run_on_production("reset_household.py")
    assert "railway ssh -- python reset_household.py" in reset
    assert "railway run" not in reset
    # The dry run is still offered, as the safe first step.
    assert "create_household.py --list" in reset

    create = how_to_run_on_production("create_household.py")
    assert "railway ssh -- python create_household.py" in create
    assert "railway run" not in create


def test_both_scripts_document_ssh_and_warn_off_run():
    """
    Pins the docstrings: `railway run` may only ever appear as the thing
    NOT to do. An edit that quietly reinstates it as the instruction fails.
    """
    for script in ("reset_household.py", "create_household.py"):
        source = open(os.path.join(REPO_ROOT, script)).read()
        assert "railway ssh --" in source, f"{script} must document the ssh form"
        assert "NOT `railway run`" in source, (
            f"{script} must explicitly warn against railway run"
        )
        assert "    railway run python" not in source, (
            f"{script} still documents `railway run` as the production command"
        )


# ---------- the scripts must actually stop ----------

def _run_script(script, db_path, extra_env=None):
    env = {**os.environ, "DB_PATH": db_path}
    env.pop("RAILWAY_VOLUME_MOUNT_PATH", None)
    env.update(extra_env or {})
    return subprocess.run(
        [sys.executable, script],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_create_script_exits_nonzero_and_creates_nothing(tmp_path):
    """
    The end-to-end guarantee: a misplaced DB_PATH must leave the filesystem
    untouched. Reading the guard's source cannot prove this; running it can.
    """
    target = tmp_path / "not-mounted" / "home_manager.db"
    result = _run_script("create_household.py", str(target))

    assert result.returncode != 0, "the script must refuse, not carry on"
    assert not target.exists(), "a decoy database was created — the whole bug"
    assert not target.parent.exists()
    assert "railway ssh --" in result.stderr


def test_reset_script_exits_nonzero_and_creates_nothing(tmp_path):
    target = tmp_path / "not-mounted" / "home_manager.db"
    result = _run_script("reset_household.py", str(target))

    assert result.returncode != 0
    assert not target.exists()
    # It must name the RESET command, not the create one.
    assert "railway ssh -- python reset_household.py" in result.stderr


def test_scripts_refuse_when_the_volume_is_named_but_not_mounted(tmp_path):
    """
    The reproduced false negative, end to end: every production variable
    present, the directory exists, nothing mounted. Before the fix this
    exited 0 and created a database it then reported households from.
    """
    mount = tmp_path / "data"
    mount.mkdir()
    target = mount / "home_manager.db"

    result = _run_script(
        "create_household.py",
        str(target),
        extra_env={"RAILWAY_VOLUME_MOUNT_PATH": str(mount)},
    )

    assert result.returncode != 0, "production vars on a laptop must be refused"
    assert not target.exists(), "a decoy database was created"
    assert "nothing is mounted there" in result.stderr
