from __future__ import annotations

import pytest

from ezsk.client import SlskdClient


class _FakeSearches:
    """Mimics slskd: responses are only served once a search is finalized."""

    def __init__(self, complete_after_stop_polls: int = 2):
        self.responses = [{"username": "someone", "files": []}]
        self.stopped = False
        self.deleted = False
        self._polls_since_stop = 0
        self._complete_after = complete_after_stop_polls

    def state(self, search_id):
        return {"id": search_id, "isComplete": False, "responseCount": 42}

    def search_responses(self, search_id):
        # slskd returns nothing while the search is still running, and only
        # starts serving results a moment after it has been stopped.
        if not self.stopped:
            return []
        self._polls_since_stop += 1
        if self._polls_since_stop < self._complete_after:
            return []
        return self.responses

    def stop(self, search_id):
        self.stopped = True

    def delete(self, search_id):
        self.deleted = True


class _FakeSlskd:
    def __init__(self, searches):
        self.searches = searches


@pytest.fixture
def client(mock_config, monkeypatch):
    searches = _FakeSearches()
    monkeypatch.setattr(
        "slskd_api.SlskdClient", lambda **kw: _FakeSlskd(searches)
    )
    c = SlskdClient(mock_config)
    c.fake_searches = searches
    return c


def test_search_returns_responses_after_timeout(client, monkeypatch):
    """A search that never reports isComplete must still return its responses.

    Popular queries keep receiving results, so slskd's search timeout keeps
    being pushed out and the search is still InProgress when we give up on it.
    Stopping it makes the responses available -- but only after a short delay.
    """
    monkeypatch.setattr(client, "start_search", lambda q, t: "search-1")
    monkeypatch.setattr("ezsk.client.time.sleep", lambda s: None)

    search_id, responses = client.search("radiohead creep", timeout_ms=1)

    assert search_id == "search-1"
    assert responses == client.fake_searches.responses
    assert client.fake_searches.stopped
    assert client.fake_searches.deleted


class _FakeSearchRegistry:
    """Mimics slskd's search list: enumerable, deletable by id."""

    def __init__(self, ids):
        self.ids = list(ids)
        self.deleted: list[str] = []

    def get_all(self):
        return [{"id": i} for i in self.ids]

    def delete(self, search_id):
        self.deleted.append(search_id)
        self.ids.remove(search_id)

    def search_text(self, searchText, searchTimeout, responseLimit):
        new_id = f"new-{len(self.ids)}"
        self.ids.append(new_id)
        return {"id": new_id}


@pytest.fixture
def registry_client(mock_config, monkeypatch):
    registry = _FakeSearchRegistry(["stale-1", "stale-2"])
    monkeypatch.setattr("slskd_api.SlskdClient", lambda **kw: _FakeSlskd(registry))
    c = SlskdClient(mock_config)
    c.fake_registry = registry
    return c


def test_cleanup_searches_spares_searches_another_thread_has_in_flight(registry_client):
    """The purge must not delete a sibling worker's live search.

    cleanup_searches() deletes by enumerating the server, and it runs from
    inside the search path itself -- one worker hitting slskd's "search
    history full" 400 used to wipe out every other worker's in-flight search,
    leaving them to see zero responses and record a false miss.
    """
    from ezsk.client import active_search_count

    live = registry_client.start_search("artist title")
    assert active_search_count() == 1

    deleted = registry_client.cleanup_searches()

    assert deleted == 2
    assert set(registry_client.fake_registry.deleted) == {"stale-1", "stale-2"}
    assert live in registry_client.fake_registry.ids

    registry_client.delete_search(live)
    assert active_search_count() == 0


def test_cleanup_searches_sweeps_everything_at_a_cold_start(registry_client):
    """run_batch's startup sweep has nothing of its own to protect."""
    live = registry_client.start_search("artist title")

    deleted = registry_client.cleanup_searches(spare_active=False)

    assert deleted == 3
    assert live in registry_client.fake_registry.deleted

    registry_client.delete_search(live)


@pytest.mark.parametrize("text, ok", [
    ("i d l e", False),
    ("y", False),
    ("", False),
    ("-ab c", False),
    ("model 500 i d l e", True),
    ("-ab cd", True),
])
def test_searchable_mirrors_the_rule_slskd_enforces(text, ok):
    """slskd answers 400 to a search with no word longer than one letter.

    Seen live: "Model 500 - I.D.L.E." searched by title alone is "i d l e",
    and slskd refuses it with "Search query must contain at least one
    non-exclusion term with length greater than 1". Words starting with "-"
    are exclusions and don't count.
    """
    from ezsk.client import searchable

    assert searchable(text) is ok


def test_start_search_refuses_an_unsearchable_query_without_asking_slskd(registry_client):
    from ezsk.client import SearchRejected, active_search_count

    before = list(registry_client.fake_registry.ids)

    with pytest.raises(SearchRejected):
        registry_client.start_search("i d l e")

    assert registry_client.fake_registry.ids == before, "nothing should reach slskd"
    assert active_search_count() == 0


SLSKD_REFUSAL = (
    "Search query must contain at least one non-exclusion term "
    "with length greater than 1 (Parameter 'query')"
)


def _slskd_http_error(status: int, reason: str, body: str):
    """The HTTPError slskd_api raises: built by raise_for_status(), body and all."""
    import requests

    resp = requests.Response()
    resp.status_code = status
    resp.reason = reason
    resp.url = "http://localhost:5030/api/v0/searches"
    resp._content = body.encode()
    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        return e
    raise AssertionError("raise_for_status() should have raised")


def test_start_search_keeps_slskds_reason_for_a_refusal(registry_client, monkeypatch):
    """raise_for_status() keeps only the status and URL; slskd's reason is the body.

    Without it every refusal was recorded -- in state.json, the UI and the
    log -- as a bare "400 Client Error: Bad Request for url: ...".
    """
    import requests

    def refuse(**kwargs):
        raise _slskd_http_error(400, "Bad Request", f'"{SLSKD_REFUSAL}"')

    monkeypatch.setattr(registry_client.fake_registry, "search_text", refuse)

    with pytest.raises(requests.HTTPError) as exc:
        registry_client.start_search("model 500 i d l e")

    message = str(exc.value)
    assert message.startswith("400 Client Error"), "status prefix drives retry handling"
    assert message.endswith(f": {SLSKD_REFUSAL}")
    assert exc.value.response.status_code == 400


class _FakeServer:
    def __init__(self, app_state):
        self._app = app_state
        self.connects = 0
        self.disconnects = 0

    def connect(self):
        self.connects += 1
        self._app.connected = True

    def disconnect(self):
        self.disconnects += 1
        self._app.connected = False


class _FakeApplication:
    def __init__(self):
        self.connected = False

    def state(self):
        return {"server": {"isConnected": self.connected, "isLoggedIn": self.connected}}


class _FakeSlskdServer:
    def __init__(self):
        self.application = _FakeApplication()
        self.server = _FakeServer(self.application)


def test_only_one_thread_reconnects_to_soulseek(mock_config, monkeypatch):
    """Concurrent 409s must not each bounce the shared Soulseek session.

    Two search workers hitting a 409 at the same moment both used to call
    ensure_connected(); the second one's reconnect lands on the session the
    first just rebuilt, knocking its search off the network. Observed live
    on the first batch after a cold slskd start.
    """
    import threading

    fake = _FakeSlskdServer()
    monkeypatch.setattr("slskd_api.SlskdClient", lambda **kw: fake)
    monkeypatch.setattr("ezsk.client.time.sleep", lambda s: None)

    clients = [SlskdClient(mock_config) for _ in range(6)]
    start = threading.Barrier(len(clients))

    def run(c):
        start.wait()
        c.ensure_connected()

    threads = [threading.Thread(target=run, args=(c,)) for c in clients]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert fake.server.connects == 1, (
        f"the session was rebuilt {fake.server.connects} times, expected once"
    )
    assert fake.server.disconnects == 0
    assert fake.application.connected
