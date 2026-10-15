import time

from tokenlens.rcs import RCSStore


def _store(tmp_path, **kw):
    defaults = dict(directory=str(tmp_path / "rcs"), enabled=True)
    defaults.update(kw)
    return RCSStore(**defaults)


class TestBasics:
    def test_disabled_returns_none(self, tmp_path):
        store = RCSStore(str(tmp_path / "rcs"), enabled=False)
        assert store.put("hello") is None
        assert store.get("anykey") is None

    def test_put_get_roundtrip(self, tmp_path):
        store = _store(tmp_path)
        key = store.put("the full original content")
        assert key is not None
        assert store.get(key) == "the full original content"

    def test_get_missing_returns_none(self, tmp_path):
        store = _store(tmp_path)
        assert store.get("does-not-exist") is None

    def test_dedup_same_content_same_key(self, tmp_path):
        store = _store(tmp_path)
        k1 = store.put("identical")
        k2 = store.put("identical")
        assert k1 == k2
        assert store.stats()["entries"] == 1


class TestPerEntryCap:
    def test_oversized_content_not_stored(self, tmp_path):
        store = _store(tmp_path, max_entry_bytes=10)
        key = store.put("this content is definitely longer than ten bytes")
        assert key is None


class TestTTL:
    def test_expired_entry_purged(self, tmp_path):
        store = _store(tmp_path, ttl_seconds=0)  # everything immediately expired
        key = store.put("ephemeral")
        # ttl=0 means created_at < now on the next call -> purged
        time.sleep(0.01)
        assert store.get(key) is None

    def test_live_entry_survives(self, tmp_path):
        store = _store(tmp_path, ttl_seconds=3600)
        key = store.put("durable")
        assert store.get(key) == "durable"


class TestLRUCountCap:
    def test_evicts_when_over_max_entries(self, tmp_path):
        store = _store(tmp_path, max_entries=3)
        keys = [store.put(f"content-{i}") for i in range(5)]
        # Only 3 should remain.
        assert store.stats()["entries"] == 3
        # The two oldest (least recently accessed) should be gone.
        assert store.get(keys[0]) is None
        assert store.get(keys[1]) is None
        assert store.get(keys[4]) == "content-4"

    def test_access_protects_from_eviction(self, tmp_path):
        store = _store(tmp_path, max_entries=2)
        k0 = store.put("zero")
        k1 = store.put("one")
        # Touch k0 so it becomes most-recently-used.
        assert store.get(k0) == "zero"
        # Adding a third evicts the LRU, which is now k1 (not k0).
        k2 = store.put("two")
        assert store.get(k0) == "zero"
        assert store.get(k2) == "two"
        assert store.get(k1) is None


class TestLRUByteCap:
    def test_evicts_when_over_max_bytes(self, tmp_path):
        # Each entry ~100 bytes; cap at 250 bytes -> at most 2 entries.
        blob = "x" * 100
        store = _store(tmp_path, max_bytes=250, max_entries=1000)
        for i in range(5):
            store.put(blob + str(i))
        assert store.stats()["bytes"] <= 250
        assert store.stats()["entries"] <= 2


class TestStats:
    def test_stats_reports_bounds(self, tmp_path):
        store = _store(tmp_path, max_bytes=999, max_entries=7, ttl_seconds=60)
        store.put("a")
        s = store.stats()
        assert s["enabled"] is True
        assert s["entries"] == 1
        assert s["max_bytes"] == 999
        assert s["max_entries"] == 7
        assert s["ttl_seconds"] == 60
