"""The deployment scripts, run for real (without root, systemd or network access).

setup-server.sh is exercised through DESTDIR, which makes it write below a temporary directory and skip
the system changes. deploy.sh itself needs pip and a git remote, so it was checked by hand in a fresh
clone; here only its argument handling is tested.
"""

import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from app.database import init_db, make_engine
from app.services import users

ROOT = Path(__file__).parent.parent
DEPLOY = ROOT / "deploy"
pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def run(script, *args, env=None, input_text=None):
    full_env = {**os.environ, **(env or {})}
    return subprocess.run(
        ["bash", str(DEPLOY / script), *args], cwd=ROOT, env=full_env, input=input_text, text=True, capture_output=True
    )


@pytest.fixture
def database(tmp_path):
    """A database file with one account, and the environment variables that point the scripts at it."""
    path = tmp_path / "data" / "seminar.db"
    engine = make_engine(f"sqlite:///{path}")
    init_db(engine)
    from sqlalchemy.orm import Session

    with Session(engine) as db:
        users.create_user(db, "alice", "Alice", "a-long-password")
    engine.dispose()
    env = {"LSP_DATABASE_URL": f"sqlite:///{path}", "PYTHON": sys.executable, "ENV_FILE": str(tmp_path / "absent.env")}
    return path, env


# --- setup-server.sh ---------------------------------------------------------------------------------------


def setup(tmp_path, *args, **extra_env):
    return run("setup-server.sh", *args, env={"DESTDIR": str(tmp_path / "root"), **extra_env})


def test_setup_writes_a_private_config_with_a_fresh_secret(tmp_path):
    result = setup(tmp_path)
    assert result.returncode == 0, result.stderr
    env_file = tmp_path / "root" / "etc" / "lab-seminar-planner" / "env"
    assert (env_file.stat().st_mode & 0o777) == 0o600
    values = dict(line.split("=", 1) for line in env_file.read_text().splitlines() if line and not line.startswith("#"))
    assert len(values["LSP_SECRET_KEY"]) >= 48
    assert values["LSP_DATABASE_URL"] == "sqlite:////var/lib/lab-seminar-planner/seminar.db"
    assert values["LSP_COOKIE_SECURE"] == "true" and values["LSP_BIND_HOST"] == "127.0.0.1"
    assert ((tmp_path / "root" / "var" / "lib" / "lab-seminar-planner").stat().st_mode & 0o777) == 0o700


def secret_of(base):
    env_file = base / "root" / "etc" / "lab-seminar-planner" / "env"
    return next(line.split("=", 1)[1] for line in env_file.read_text().splitlines() if line.startswith("LSP_SECRET_KEY="))


def test_setup_secrets_differ_between_servers_and_survive_a_rerun(tmp_path):
    setup(tmp_path / "a")
    setup(tmp_path / "b")
    assert secret_of(tmp_path / "a") != secret_of(tmp_path / "b")
    before = secret_of(tmp_path / "a")
    rerun = setup(tmp_path / "a")
    assert rerun.returncode == 0 and "Keeping the existing configuration" in rerun.stdout
    assert secret_of(tmp_path / "a") == before


def test_setup_no_https_turns_off_the_secure_cookie_flag(tmp_path):
    assert setup(tmp_path, "--no-https").returncode == 0
    assert "LSP_COOKIE_SECURE=false" in (tmp_path / "root" / "etc" / "lab-seminar-planner" / "env").read_text()


def test_setup_installs_complete_systemd_units(tmp_path):
    setup(tmp_path)
    units = tmp_path / "root" / "etc" / "systemd" / "system"
    names = sorted(p.name for p in units.iterdir())
    assert names == ["lab-seminar-planner-backup.service", "lab-seminar-planner-backup.timer", "lab-seminar-planner.service"]
    for unit in units.iterdir():
        text = unit.read_text()
        assert "@" not in text.replace("@reboot", ""), f"{unit.name} still has a placeholder"
    service = (units / "lab-seminar-planner.service").read_text()
    assert f"WorkingDirectory={ROOT}" in service and f"ExecStart={ROOT}/.venv/bin/python -m uvicorn app.main:create_app --factory" in service
    assert "User=lab-seminar" in service and "EnvironmentFile=/etc/lab-seminar-planner/env" in service
    assert "ReadWritePaths=/var/lib/lab-seminar-planner" in service and "ProtectSystem=strict" in service
    assert "${LSP_BIND_HOST}" in service  # left for systemd to expand from the environment file
    assert f"ExecStart={ROOT}/deploy/backup.sh" in (units / "lab-seminar-planner-backup.service").read_text()


def test_setup_respects_custom_locations_and_user(tmp_path):
    result = setup(tmp_path, SERVICE_USER="planner", STATE_DIR="/srv/planner/data", ENV_FILE="/etc/planner.env")
    assert result.returncode == 0, result.stderr
    service = (tmp_path / "root" / "etc" / "systemd" / "system" / "lab-seminar-planner.service").read_text()
    assert "User=planner" in service and "ReadWritePaths=/srv/planner/data" in service and "EnvironmentFile=/etc/planner.env" in service
    assert "sqlite:////srv/planner/data/seminar.db" in (tmp_path / "root" / "etc" / "planner.env").read_text()


@pytest.mark.parametrize("variable, value", [("STATE_DIR", "/var/lib/with space"), ("SERVICE_USER", "bad;user"), ("ENV_FILE", "/etc/$(x)")])
def test_setup_refuses_unsafe_values(tmp_path, variable, value):
    result = setup(tmp_path, **{variable: value})
    assert result.returncode == 1 and "unsupported character" in result.stderr
    assert not list((tmp_path / "root").rglob("*.service"))  # it stopped before installing anything


def test_setup_needs_root_unless_testing(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("running as root")
    result = run("setup-server.sh", env={"DESTDIR": ""})
    assert result.returncode == 1 and "run as root" in result.stderr


# --- backup.sh -------------------------------------------------------------------------------------------------


def test_backup_copies_the_database_consistently_and_privately(database):
    path, env = database
    result = run("backup.sh", env=env)
    assert result.returncode == 0, result.stderr
    (backup,) = (path.parent / "backups").glob("seminar-*.db")
    assert (backup.stat().st_mode & 0o777) == 0o600 and ((path.parent / "backups").stat().st_mode & 0o777) == 0o700
    copy = sqlite3.connect(backup)
    assert copy.execute("select username from users").fetchall() == [("alice",)]
    assert copy.execute("pragma integrity_check").fetchone()[0] == "ok"
    copy.close()


def test_backup_keeps_only_the_newest_copies(database):
    path, env = database
    backups = path.parent / "backups"
    backups.mkdir()
    for day in range(1, 6):
        (backups / f"seminar-2020010{day}-000000-1.db").write_bytes(b"old")
    (backups / "seminar-20200106-000000-1.db.partial").write_bytes(b"interrupted")
    result = run("backup.sh", env={**env, "KEEP": "3"})
    assert result.returncode == 0, result.stderr
    kept = sorted(p.name for p in backups.iterdir())
    assert len(kept) == 3
    assert kept[:2] == ["seminar-20200104-000000-1.db", "seminar-20200105-000000-1.db"]  # the two newest old ones
    assert kept[2] > kept[1]  # and the backup just made
    assert not list(backups.glob("*.partial"))


def test_backup_refuses_a_damaged_database_and_leaves_nothing_behind(tmp_path):
    """Damage only the index so SQLite's page-by-page copy succeeds and just the integrity check can notice."""
    path = tmp_path / "data" / "seminar.db"
    path.parent.mkdir()
    db = sqlite3.connect(path)
    db.execute("pragma journal_mode=delete")
    db.execute("create table t (id integer primary key, v text)")
    db.execute("create index ix on t(v)")
    db.executemany("insert into t(v) values (?)", [(f"value-{i:05d}",) for i in range(3000)])
    db.commit()
    db.close()
    data = bytearray(path.read_bytes())
    needle = b"value-01500"
    last = data.rindex(needle)  # the second copy of the value is the index entry
    data[last : last + len(needle)] = b"value-99999"
    path.write_bytes(data)
    assert "missing from index" in sqlite3.connect(path).execute("pragma integrity_check").fetchone()[0]  # really damaged

    env = {"LSP_DATABASE_URL": f"sqlite:///{path}", "PYTHON": sys.executable, "ENV_FILE": str(tmp_path / "absent.env")}
    result = run("backup.sh", env=env)
    assert result.returncode == 1 and "integrity check" in result.stderr
    assert list((path.parent / "backups").iterdir()) == []  # neither a backup nor a .partial file


def test_backup_without_a_database_is_not_an_error(tmp_path):
    env = {"LSP_DATABASE_URL": f"sqlite:///{tmp_path}/none.db", "PYTHON": sys.executable, "ENV_FILE": str(tmp_path / "absent.env")}
    result = run("backup.sh", env=env)
    assert result.returncode == 0 and "nothing to back up" in result.stdout
    assert not (tmp_path / "backups").exists()


@pytest.mark.parametrize("keep", ["abc", "0", "-1", "2.5"])
def test_backup_rejects_a_bad_keep_value(database, keep):
    result = run("backup.sh", env={**database[1], "KEEP": keep})
    assert result.returncode == 1 and "KEEP" in result.stderr


def run_with_config_file(script, tmp_path, database_path, *args, input_text=None):
    """Run a script that must find the database through the configuration file, not the environment."""
    config = tmp_path / "service.env"
    config.write_text(f"LSP_SECRET_KEY=x\nLSP_DATABASE_URL=sqlite:///{database_path}\n")
    env = {k: v for k, v in os.environ.items() if k != "LSP_DATABASE_URL"} | {"ENV_FILE": str(config), "PYTHON": sys.executable}
    return subprocess.run(["bash", str(DEPLOY / script), *args], env=env, cwd=ROOT, input=input_text, text=True, capture_output=True)


def test_backup_reads_the_database_location_from_the_config_file(tmp_path, database):
    path, _ = database
    result = run_with_config_file("backup.sh", tmp_path, path)
    assert result.returncode == 0, result.stderr
    assert list((path.parent / "backups").glob("seminar-*.db"))


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read any file")
@pytest.mark.parametrize("script, args", [("backup.sh", []), ("lsp", ["init-db"])])
def test_an_unreadable_config_file_is_an_error_not_a_silent_fallback_to_defaults(tmp_path, script, args):
    config = tmp_path / "root-only.env"
    config.write_text("LSP_DATABASE_URL=sqlite:////somewhere/else.db\n")
    config.chmod(0o000)
    env = {k: v for k, v in os.environ.items() if k != "LSP_DATABASE_URL"} | {"ENV_FILE": str(config), "PYTHON": sys.executable}
    result = subprocess.run(["bash", str(DEPLOY / script), *args], env=env, cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 1 and "cannot read" in result.stderr and "sudo" in result.stderr


def test_settings_already_in_the_environment_are_enough_even_if_the_file_is_unreadable(tmp_path, database):
    """This is how systemd runs the backup service: it reads the file as root and passes the values in."""
    path, env = database
    config = tmp_path / "root-only.env"
    config.write_text("x=y\n")
    config.chmod(0o000)
    result = run("backup.sh", env={**env, "ENV_FILE": str(config)})
    assert result.returncode == 0, result.stderr
    assert list((path.parent / "backups").glob("seminar-*.db"))


# --- lsp wrapper ------------------------------------------------------------------------------------------------------


def test_lsp_runs_cli_commands_against_the_configured_database(tmp_path):
    db_path = tmp_path / "prod" / "seminar.db"
    init = run_with_config_file("lsp", tmp_path, db_path, "init-db")
    assert init.returncode == 0 and db_path.exists(), init.stderr
    created = run_with_config_file("lsp", tmp_path, db_path, "create-user", "bob", "--display-name", "Bob",
                                   input_text="a-long-password\na-long-password\n")
    assert created.returncode == 0, created.stderr
    assert sqlite3.connect(db_path).execute("select username from users").fetchall() == [("bob",)]


def test_lsp_passes_command_failures_through(database):
    result = run("lsp", "seed-demo", "--topics-file", "/nonexistent.json", env=database[1])
    assert result.returncode == 1 and "Sample topics not found" in result.stderr


# --- deploy.sh argument handling and CI wiring ------------------------------------------------------------------------------------


def test_deploy_help_and_bad_arguments():
    helped = run("deploy.sh", "--help")
    assert helped.returncode == 0 and "--ref REF" in helped.stdout and "--no-restart" in helped.stdout
    assert "set -euo" not in helped.stdout  # the help text stops where the code starts
    assert run("deploy.sh", "--bogus").returncode == 1
    missing = run("deploy.sh", "--ref")
    assert missing.returncode == 1 and "--ref needs a value" in missing.stderr


def fake_python(tmp_path, version):
    """An interpreter that evaluates the script's own `-c` checks but reports `version`.

    It runs the real Python with sys.version_info patched, so a check that is wrong (or missing) is noticed.
    Anything else (such as `-m venv`) is passed straight through.
    """
    major, minor = (int(part) for part in version.split(".")[:2])
    script = tmp_path / f"python-{version}"
    script.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "-c" ]; then\n'
        f"  exec {sys.executable} -c 'import sys\n"
        f'sys.version_info = ({major}, {minor}, 0, "final", 0)\n'
        f'sys.version = "{version} (fake)"\n'
        "exec(sys.argv[1])' \"$2\"\n"
        "fi\n"
        f'exec {sys.executable} "$@"\n'
    )
    script.chmod(0o755)
    return str(script)


def stub_venv(tmp_path):
    """A virtual environment whose python fails at once, so a test can never run a real pip install."""
    stub = tmp_path / "stub-venv" / "bin" / "python"
    stub.parent.mkdir(parents=True)
    stub.write_text("#!/bin/sh\necho 'stub python: stopped before installing anything' >&2\nexit 3\n")
    stub.chmod(0o755)
    return str(stub.parent.parent)


def test_deploy_refuses_an_old_python_and_says_which_version_it_found(tmp_path):
    result = run("deploy.sh", "--no-restart", env={"PYTHON": fake_python(tmp_path, "3.10.4"), "VENV": stub_venv(tmp_path)})
    assert result.returncode == 1 and "Python 3.11 or newer is required" in result.stderr and "3.10.4" in result.stderr
    assert "stub python" not in result.stderr  # it stopped before reaching the environment step


def test_deploy_refuses_an_interpreter_that_does_not_exist(tmp_path):
    result = run("deploy.sh", "--no-restart", env={"PYTHON": str(tmp_path / "no-such-python"), "VENV": stub_venv(tmp_path)})
    assert result.returncode == 1 and "not usable" in result.stderr


@pytest.mark.parametrize("version", ["3.11.0", "3.12.4", "3.13.1"])
def test_deploy_accepts_python_3_11_and_newer(tmp_path, version):
    """The version check passes; deploy.sh then stops at the stub environment, before any installation."""
    result = run("deploy.sh", "--no-restart", env={"PYTHON": fake_python(tmp_path, version), "VENV": stub_venv(tmp_path)})
    assert "Python 3.11 or newer is required" not in result.stderr
    assert "Virtual environment" in result.stdout and "stub python" in result.stderr


def test_every_shell_script_is_executable_and_has_a_bash_shebang():
    scripts = [*DEPLOY.glob("*.sh"), DEPLOY / "lsp", ROOT / "scripts" / "ci.sh", ROOT / "scripts" / "smoke_test.py"]
    for script in scripts:
        assert os.access(script, os.X_OK) or script.name == "common.sh", f"{script} is not executable"
        assert script.read_text().startswith("#!/usr/bin/env "), script


def test_ci_checks_the_deployment_scripts_too():
    ci = (ROOT / "scripts" / "ci.sh").read_text()
    assert "find scripts deploy" in ci and "bash -n" in ci
