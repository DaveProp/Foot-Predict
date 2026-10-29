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


def fin(i, day, home, away, gh, ga):
    return {"id": i, "utcDate": f"2026-10-{day:02d}T18:00:00Z", "status": "FINISHED",
            "competition": {"code": "PL"}, "homeTeam": {"name": home, "shortName": home},
            "awayTeam": {"name": away, "shortName": away}, "score": {"fullTime": {"home": gh, "away": ga}}}


def test_form_keeps_last_six_oldest_first():
    from data_provider import form_from_matches, form_summary
    games = [fin(i, i, "A", "B" if i % 2 else "C", 2, 1) for i in range(1, 9)]  # A gagne 8 fois à domicile
    form = form_from_matches(games)
    assert [g["d"] for g in form["A"]] == [f"2026-10-{d:02d}" for d in range(3, 9)]
    assert all(g["r"] == "V" and g["home"] for g in form["A"])
    s = form_summary(form["A"])
    assert (s["n"], s["w"], s["pts"], s["gf"], s["ga"]) == (6, 6, 18, 12, 6)


def test_form_result_is_from_each_team_point_of_view():
    from data_provider import form_from_matches
    form = form_from_matches([fin(1, 5, "A", "B", 0, 3), fin(1, 5, "A", "B", 0, 3)])  # doublon ignoré
    assert [g["r"] for g in form["A"]] == ["D"] and [g["r"] for g in form["B"]] == ["V"]
    assert form["B"][0]["home"] is False and form["B"][0]["gf"] == 3


def test_unfinished_or_unscored_matches_are_ignored_in_form():
    from data_provider import form_from_matches
    live = fin(2, 6, "A", "B", None, None)
    sched = dict(fin(3, 7, "A", "B", 1, 0), status="TIMED")
    assert form_from_matches([live, sched]) == {}
