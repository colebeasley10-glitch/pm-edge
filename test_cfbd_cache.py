from cfbd_client import CachedCFBD


class FakeClient:
    def __init__(self):
        self.calls = 0
        self.fail = False

    def games(self, year):
        self.calls += 1
        if self.fail:
            raise RuntimeError("429 Too Many Requests")
        return [{"id": self.calls}]


def test_cache_reuses_until_ttl_then_refreshes(tmp_path):
    now = [1000.0]
    fake = FakeClient()
    c = CachedCFBD(tmp_path / "c.json", client=fake, clock=lambda: now[0])
    assert c.games(2026) == [{"id": 1}]
    now[0] += 3600
    assert c.games(2026) == [{"id": 1}] and fake.calls == 1
    now[0] += 12 * 3600
    assert c.games(2026) == [{"id": 2}] and fake.calls == 2


def test_falls_back_to_stale_copy_on_error_and_survives_restart(tmp_path):
    now = [1000.0]
    fake = FakeClient()
    c = CachedCFBD(tmp_path / "c.json", client=fake, clock=lambda: now[0])
    c.games(2026)
    c.save()
    fake.fail = True
    now[0] += 13 * 3600
    c2 = CachedCFBD(tmp_path / "c.json", client=fake, clock=lambda: now[0])
    assert c2.games(2026) == [{"id": 1}]
    assert c2.errors and "429" in c2.errors[0]


def test_no_key_and_no_cache_is_fatal(tmp_path, monkeypatch):
    monkeypatch.delenv("CFBD_API_KEY", raising=False)
    c = CachedCFBD(tmp_path / "c.json")
    try:
        c.games(2026)
    except RuntimeError as exc:
        assert "CFBD_API_KEY" in str(exc)
    else:
        raise AssertionError("expected failure")
