import math
from datetime import datetime, timedelta, timezone

import access
import coupons
from model import market_probs

TEAMS = {
    "A": {"attack": 1.7, "defense": 0.65}, "B": {"attack": 0.9, "defense": 1.1},
    "C": {"attack": 1.4, "defense": 0.8}, "D": {"attack": 1.0, "defense": 1.0},
    "E": {"attack": 1.2, "defense": 0.9}, "F": {"attack": 1.1, "defense": 1.1},
    "G": {"attack": 1.5, "defense": 0.75}, "H": {"attack": 0.8, "defense": 1.2},
    "I": {"attack": 1.3, "defense": 0.85}, "J": {"attack": 1.0, "defense": 1.05},
    "K": {"attack": 1.6, "defense": 0.7}, "L": {"attack": 0.95, "defense": 1.0},
}


def fake_matches():
    names = list(TEAMS)
    return [
        {"id": i, "league": "Ligue test", "home": names[2 * i], "away": names[2 * i + 1],
         "status": "scheduled", "reliable": True}
        for i in range(6)
    ]


def test_valid_code_is_accepted():
    code, expires = access.make_code(30)
    assert access.check_code(code) == expires
    assert access.check_code(code.lower()) == expires


def test_tampered_or_garbage_code_is_rejected():
    code, _ = access.make_code(30)
    assert access.check_code(code[:-1] + ("0" if code[-1] != "0" else "1")) is None
    assert access.check_code("") is None
    assert access.check_code("FP-20990101-ABCD-00000000") is None


def test_expired_code_is_rejected():
    past = (datetime.now(timezone.utc) - timedelta(days=2)).date()
    body = f"FP-{past:%Y%m%d}-AB12"
    assert access.check_code(f"{body}-{access._sig(body)}") is None


def test_market_probs_are_coherent():
    p = market_probs(1.8, 1.0)
    assert math.isclose(p["1"] + p["X"] + p["2"], 1.0, abs_tol=1e-6)
    assert math.isclose(p["btts_yes"] + p["btts_no"], 1.0, abs_tol=1e-6)
    assert p["over_1_5"] > p["over_2_5"]


def test_coupons_respect_odds_ranges_and_one_pick_per_match():
    result = coupons.build_coupons(fake_matches(), TEAMS)
    assert [c["key"] for c in result] == ["confiance", "equilibre", "risque"]
    for c in result:
        if c["empty"]:
            continue
        assert c["min"] <= c["total_odds"] <= c["max"]
        ids = [leg["match_id"] for leg in c["legs"]]
        assert len(ids) == len(set(ids))
    assert not result[0]["empty"]


def test_daily_contains_big_matches_with_analysis():
    d = coupons.build_daily(fake_matches(), TEAMS, {"scheduled"})
    assert len(d["big_matches"]) == 3
    big = d["big_matches"][0]
    assert math.isclose(big["home_win"] + big["draw"] + big["away_win"], 1.0, abs_tol=1e-3)
    assert big["analysis"] and "garanti" in d["note"]


def test_unreliable_matches_are_excluded():
    ms = fake_matches()
    for m in ms:
        m["reliable"] = False
    assert coupons.build_daily(ms, TEAMS, {"scheduled"})["coupons"] == []


def test_analysis_mentions_recent_form():
    s = {"n": 6, "w": 4, "d": 1, "l": 1, "gf": 11, "ga": 4, "pts": 13}
    m = dict(fake_matches()[0], form={"home": {"games": [], "summary": s}, "away": {"games": [], "summary": s}})
    d = coupons.analyse_match(m, TEAMS)
    assert "4 victoire(s)" in d["analysis"] and "6 derniers matchs" in d["analysis"]
    assert "Forme" not in coupons.analyse_match(fake_matches()[0], TEAMS)["analysis"]
