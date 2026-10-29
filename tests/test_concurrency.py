"""Thread-safety of the structures shared by the search and download stages.

Searches and downloads both run on worker threads, and the web layer spawns
its own on-demand tasks alongside a running batch, so state.json and the
library index are written from several threads at once.
"""
from __future__ import annotations

import threading

from ezsk.index import find_in_index, index_add
from ezsk.state import load_state, state_txn, update_entry


def test_state_txn_does_not_lose_concurrent_updates(tmp_path):
    """Every thread's entry survives.

    state.json is rewritten whole, so an unsynchronized load -> mutate -> save
    drops whichever writer loaded first. With 16 threads x 20 writes the
    unlocked version loses most of them.
    """
    state_file = str(tmp_path / "state.json")
    threads_count, per_thread = 16, 20
    start = threading.Barrier(threads_count)

    def worker(n: int) -> None:
        start.wait()
        for i in range(per_thread):
            with state_txn(state_file) as state:
                update_entry(state, f"artist {n} - track {i}", status="completed")

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(threads_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    state = load_state(state_file)
    assert len(state) == threads_count * per_thread
    queries = {e["query"] for e in state.values()}
    for n in range(threads_count):
        for i in range(per_thread):
            assert f"artist {n} - track {i}" in queries


def test_find_in_index_survives_a_concurrent_index_add(tmp_path):
    """The scan must not blow up when a download worker indexes a new file.

    find_in_index() used to iterate the live dict; index_add() assigning into
    it mid-scan raises "dictionary changed size during iteration".
    """
    output_dir = str(tmp_path)
    index = {f"seeded artist {i} track {i}": str(tmp_path / f"{i}.flac") for i in range(200)}
    adds = 300
    done = threading.Event()
    errors: list[BaseException] = []

    def writer() -> None:
        try:
            for i in range(adds):
                index_add(index, output_dir, f"new artist {i} - new track {i}", "/tmp/x.flac")
        except BaseException as e:  # noqa: BLE001 - recorded and re-asserted below
            errors.append(e)
        finally:
            done.set()

    t = threading.Thread(target=writer)
    t.start()
    try:
        while not done.is_set():
            find_in_index(index, "seeded artist 7 track 7")
    except BaseException as e:  # noqa: BLE001
        errors.append(e)
    finally:
        t.join()

    assert not errors, f"concurrent index access raised: {errors[0]!r}"
    assert len(index) == 200 + adds
