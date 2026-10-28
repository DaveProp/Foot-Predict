from data_provider import parse_fixture, strengths_from_table


def row(name, played, gf, ga):
    return {"team": {"name": name}, "all": {"played": played, "goals": {"for": gf, "against": ga}}}


def test_strengths_reflect_goals():
    table = [row("A", 10, 25, 8), row("B", 10, 12, 12), row("C", 10, 8, 25)]
    s = strengths_from_table(table)
    assert s["A"]["attack"] > 1 > s["C"]["attack"]
    assert s["A"]["defense"] < 1 < s["C"]["defense"]


def test_few_games_stay_close_to_average():
    table = [row("A", 1, 4, 0), row("B", 1, 0, 4)]
    s = strengths_from_table(table)
    assert s["A"]["attack"] < 1.7  # fortement ramené vers 1.0


def test_parse_live_fixture():
    f = {
        "fixture": {"id": 7, "status": {"short": "2H", "elapsed": 63}},
        "league": {"name": "Ligue 1"},
        "teams": {"home": {"name": "X"}, "away": {"name": "Y"}},
        "goals": {"home": 2, "away": None},
    }
    m = parse_fixture(f)
    assert m["status"] == "live" and m["minute"] == 63 and m["score"] == [2, 0]


def test_league_label_includes_country_and_id():
    f = {
        "fixture": {"id": 9, "status": {"short": "NS", "elapsed": None}},
        "league": {"id": 39, "name": "Premier League", "country": "England"},
        "teams": {"home": {"name": "A"}, "away": {"name": "B"}},
        "goals": {"home": None, "away": None},
    }
    m = parse_fixture(f)
    assert m["league"] == "Premier League (England)" and m["league_id"] == 39 and m["status"] == "scheduled"
