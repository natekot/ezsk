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
