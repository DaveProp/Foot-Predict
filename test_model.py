import math

from model import predict_live, predict_prematch

STRONG = {"attack": 1.6, "defense": 0.7}
WEAK = {"attack": 0.9, "defense": 1.1}


def test_probabilities_sum_to_one():
    r = predict_prematch(STRONG, WEAK)
    assert math.isclose(r["home_win"] + r["draw"] + r["away_win"], 1.0, abs_tol=1e-3)


def test_stronger_home_team_favored():
    r = predict_prematch(STRONG, WEAK)
    assert r["home_win"] > r["away_win"]


def test_live_leader_gains_probability():
    base = predict_live(STRONG, STRONG, 60, (0, 0))
    ahead = predict_live(STRONG, STRONG, 60, (2, 0))
    assert ahead["home_win"] > base["home_win"]


def test_full_time_is_decided():
    r = predict_live(STRONG, WEAK, 90, (1, 0))
    assert r["home_win"] > 0.99


def test_red_card_hurts_team():
    base = predict_live(STRONG, STRONG, 30, (0, 0))
    red = predict_live(STRONG, STRONG, 30, (0, 0), red_home=1)
    assert red["home_win"] < base["home_win"]
