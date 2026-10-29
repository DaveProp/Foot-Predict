from datetime import datetime, timezone

from data_provider import parse_match, parse_standings, strengths_from_table


def row(name, played, gf, ga):
    return {"team": {"name": name, "shortName": name}, "playedGames": played, "goalsFor": gf, "goalsAgainst": ga}


def match(status, **extra):
    base = {
        "id": 7, "utcDate": "2026-10-28T19:00:00Z", "status": status,
        "competition": {"code": "PL", "name": "Premier League"},
        "homeTeam": {"name": "Arsenal FC", "shortName": "Arsenal"},
        "awayTeam": {"name": "Chelsea FC", "shortName": "Chelsea"},
        "score": {"fullTime": {"home": None, "away": None}},
    }
    base.update(extra)
    return base


def test_strengths_reflect_goals():
    table = [row("A", 10, 25, 8), row("B", 10, 12, 12), row("C", 10, 8, 25)]
    s = strengths_from_table(table)
    assert s["A"]["attack"] > 1 > s["C"]["attack"]
    assert s["A"]["defense"] < 1 < s["C"]["defense"]


def test_few_games_stay_close_to_average():
    table = [row("A", 1, 4, 0), row("B", 1, 0, 4)]
    s = strengths_from_table(table)
    assert s["A"]["attack"] < 1.7  # fortement ramené vers 1.0


def test_standings_use_total_table_only():
    data = {"standings": [
        {"type": "TOTAL", "table": [row("A", 10, 25, 8), row("B", 10, 8, 25)]},
        {"type": "HOME", "table": [row("Z", 5, 99, 0)]},
    ]}
    s = parse_standings(data)
    assert set(s) == {"A", "B"}


def test_scheduled_match():
    m = parse_match(match("TIMED"))
    assert m["status"] == "scheduled" and m["minute"] == 0 and m["home"] == "Arsenal" and m["league_code"] == "PL"


def test_live_match_minute_estimated_from_kickoff():
    now = datetime(2026, 10, 28, 19, 30, tzinfo=timezone.utc)
    f = match("IN_PLAY", score={"fullTime": {"home": 2, "away": None}})
    m = parse_match(f, now)
    assert m["status"] == "live" and m["minute"] == 30 and m["score"] == [2, 0]


def test_second_half_minute_skips_the_break():
    now = datetime(2026, 10, 28, 20, 20, tzinfo=timezone.utc)  # 80 min après le coup d'envoi
    assert parse_match(match("IN_PLAY"), now)["minute"] == 65


def test_halftime_and_finished():
    assert parse_match(match("PAUSED"))["minute"] == 45
    done = parse_match(match("FINISHED", score={"fullTime": {"home": 1, "away": 1}}))
    assert done["status"] == "finished" and done["minute"] == 90


def test_postponed_match_is_ignored():
    assert parse_match(match("POSTPONED")) is None
