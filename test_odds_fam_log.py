"""Odds Fam logger: appends a row through GitHub's contents API (faked here)."""
import base64
import os

import odds_fam_log
from research_lab import odds_fam as of


class Resp:
    def __init__(self, code, data=None):
        self.status_code, self._d = code, data or {}

    def json(self):
        return self._d


class FakeGitHub:
    def __init__(self):
        self.content, self.sha = None, None

    def get(self, url, **kw):
        if self.content is None:
            return Resp(404)
        return Resp(200, {"content": base64.b64encode(self.content.encode()).decode(), "sha": self.sha})

    def put(self, url, json=None, **kw):
        if json.get("sha") != self.sha:
            return Resp(409)
        self.content, self.sha = base64.b64decode(json["content"]).decode(), str(hash(json["content"]))
        return Resp(201)


def test_append_twice_keeps_both():
    os.environ["GITHUB_TOKEN"] = "x"
    try:
        gh = FakeGitHub()
        odds_fam_log.append({"of_id": "a", "pick": "Under 61", "book": "Rebet", "odds": "-110", "decimal": 1.909,
                             "stake": 10, "line_taken": "61"}, session=gh)
        odds_fam_log.append({"of_id": "b", "pick": "Luka Under 7.5 Assists", "book": "Betr", "odds": "-112",
                             "decimal": 1.893, "stake": 15, "line_taken": "7.5"}, session=gh)
        rows = of.parse_bets(gh.content)
        assert [r["of_id"] for r in rows] == ["a", "b"] and all(r["bet_id"] for r in rows)
    finally:
        os.environ.pop("GITHUB_TOKEN")
