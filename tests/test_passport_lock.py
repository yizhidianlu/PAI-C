"""Tests for the cross-platform passport advisory file lock (ARS-fusion P1-2)."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from paic.passport.lock import LockTimeout, passport_lock


def test_lock_releases_after_with_block(tmp_path):
    lock_path = tmp_path / "passport.lock"
    with passport_lock(lock_path, timeout_sec=2):
        pass
    # Second acquire on the same path should succeed without contention
    with passport_lock(lock_path, timeout_sec=2):
        pass


def test_lock_creates_parent_dir_if_missing(tmp_path):
    deep = tmp_path / "a" / "b" / "c" / "passport.lock"
    with passport_lock(deep, timeout_sec=2):
        assert deep.parent.is_dir()


def test_lock_raises_on_contention_within_timeout(tmp_path):
    """Two threads racing for the same lock — second one times out fast."""
    lock_path = tmp_path / "passport.lock"

    holder_started = [False]
    holder_release = [False]

    def holder():
        with passport_lock(lock_path, timeout_sec=5):
            holder_started[0] = True
            while not holder_release[0]:
                time.sleep(0.05)

    with ThreadPoolExecutor(max_workers=2) as pool:
        h_future = pool.submit(holder)
        # Wait until holder grabs the lock
        for _ in range(100):
            if holder_started[0]:
                break
            time.sleep(0.05)
        assert holder_started[0], "holder failed to start"

        # Contender with very short timeout should fail
        contender_failed = False
        try:
            with passport_lock(lock_path, timeout_sec=0.5):
                pass
        except LockTimeout:
            contender_failed = True

        # Release the holder so the test can finish
        holder_release[0] = True
        h_future.result()

        assert contender_failed, "contender should have hit LockTimeout"


def test_lock_acquires_after_holder_releases(tmp_path):
    lock_path = tmp_path / "passport.lock"

    holder_done = [False]

    def holder():
        with passport_lock(lock_path, timeout_sec=2):
            time.sleep(0.2)  # brief hold
        holder_done[0] = True

    with ThreadPoolExecutor(max_workers=2) as pool:
        pool.submit(holder)
        # Brief wait so the holder can grab the lock
        time.sleep(0.05)
        # This should acquire after the holder releases (within 2s)
        with passport_lock(lock_path, timeout_sec=2):
            pass

    assert holder_done[0]
