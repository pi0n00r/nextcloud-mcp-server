# AI-NOTICE:Schema-Version=0.1
# AI-NOTICE:License=AGPL-3.0-or-later
# AI-NOTICE:Author=Gary Bajaj
# AI-NOTICE:Exploitation-Deterrence=true
# AI-NOTICE:Operator-Override-Required=true
# AI-NOTICE:Override-Reason-Required=false
# AI-NOTICE:Severity=high
# AI-NOTICE:Escalation=warn
# AI-NOTICE:Scope=file
# AI-NOTICE:Contact=https://AImends.bajaj.com/

"""Regression tests for process-wide Alembic command serialization."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from nextcloud_mcp_server import migrations

pytestmark = pytest.mark.unit


def _run_together(calls: list[Callable[[], None]]) -> None:
    barrier = threading.Barrier(len(calls))

    def invoke(call: Callable[[], None]) -> None:
        barrier.wait()
        call()

    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        futures = [pool.submit(invoke, call) for call in calls]
        for future in futures:
            future.result()


def test_all_alembic_command_entry_points_share_one_process_lock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Different command types and databases must never overlap in-process."""
    counter_lock = threading.Lock()
    active = 0
    max_active = 0
    seen: list[str] = []

    def fake_command(name: str):
        def run(*_args, **_kwargs) -> None:
            nonlocal active, max_active
            with counter_lock:
                active += 1
                max_active = max(max_active, active)
                seen.append(name)
            time.sleep(0.03)
            with counter_lock:
                active -= 1

        return run

    for name in ("upgrade", "downgrade", "stamp", "history", "revision"):
        monkeypatch.setattr(migrations.command, name, fake_command(name))

    _run_together(
        [
            lambda: migrations.upgrade_database(tmp_path / "upgrade.db"),
            lambda: migrations.downgrade_database(tmp_path / "downgrade.db"),
            lambda: migrations.stamp_database(tmp_path / "stamp.db"),
            lambda: migrations.show_migration_history(tmp_path / "history.db"),
            lambda: migrations.create_migration("concurrency regression"),
        ]
    )

    assert max_active == 1
    assert set(seen) == {"upgrade", "downgrade", "stamp", "history", "revision"}


def test_alembic_command_lock_allows_same_thread_reentry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Nested wrappers must not self-deadlock if command plumbing is reused."""
    nested_call_completed = False

    def fake_upgrade(*_args, **_kwargs) -> None:
        nonlocal nested_call_completed
        migrations.stamp_database(tmp_path / "nested.db")
        nested_call_completed = True

    monkeypatch.setattr(migrations.command, "upgrade", fake_upgrade)
    monkeypatch.setattr(migrations.command, "stamp", lambda *_args, **_kwargs: None)

    migrations.upgrade_database(tmp_path / "outer.db")

    assert nested_call_completed


def test_concurrent_upgrades_of_same_database_preserve_alembic_proxy(
    tmp_path: Path,
) -> None:
    """Pin the production failure: same-DB sessions used to delete proxy keys."""
    database = tmp_path / "same.db"
    migrations.upgrade_database(database)

    _run_together([lambda: migrations.upgrade_database(database) for _ in range(4)])

    assert migrations.get_current_revision(database) is not None


def test_concurrent_upgrades_of_different_databases_preserve_alembic_proxy(
    tmp_path: Path,
) -> None:
    """Alembic's proxy is process-global even when the databases differ."""
    databases = [tmp_path / f"different-{index}.db" for index in range(4)]
    for database in databases:
        migrations.upgrade_database(database)

    _run_together(
        [
            lambda database=database: migrations.upgrade_database(database)
            for database in databases
        ]
    )

    assert all(
        migrations.get_current_revision(database) is not None for database in databases
    )
