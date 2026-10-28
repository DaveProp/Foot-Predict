"""Coupons du jour (3 paliers de cote) et analyses des grands matchs.

Les cotes sont des cotes ESTIMÉES à partir des probabilités du modèle (avec une marge type bookmaker).
Les cotes réelles des opérateurs diffèrent : un coupon n'est jamais une garantie de gain.
"""
import itertools
import json
import math
import os
import urllib.request
from datetime import datetime, timezone

from model import expected_goals, market_probs, score_matrix, summarize

BOOK_MARGIN = 0.95  # cote estimée = 0,95 / probabilité
MAX_LEGS = 5
POOL_SIZE = 24

# (clé, nom affiché, cote totale min, cote totale max, fenêtre de cote par sélection)
CATEGORIES = [
    ("confiance", "Confiance", 1.70, 2.50, (1.15, 2.50)),
    ("equilibre", "Équilibré", 3.00, 5.00, (1.30, 3.20)),
    ("risque", "Risque", 7.00, 10.00, (1.50, 4.00)),
]

CHAMPIONS_IDS = {2, 3, 848, 12, 20}


def _labels(home: str, away: str) -> dict[str, str]:
    return {
        "1": f"Victoire {home}",
        "2": f"Victoire {away}",
        "1X": f"{home} ou nul",
        "X2": f"{away} ou nul",
        "12": "Pas de match nul",
        "over_1_5": "Plus de 1,5 but",
        "over_2_5": "Plus de 2,5 buts",
        "under_3_5": "Moins de 3,5 buts",
        "btts_yes": "Les deux équipes marquent",
        "btts_no": "Les deux équipes ne marquent pas",
    }


def match_picks(match: dict, teams: dict) -> list[dict]:
    lh, la = expected_goals(teams[match["home"]], teams[match["away"]])
    probs = market_probs(lh, la)
    out = []
    for key, label in _labels(match["home"], match["away"]).items():
        p = probs[key]
        if p < 0.25:
            continue
        out.append({
            "match_id": match["id"], "match": f"{match['home']} - {match['away']}",
            "league": match["league"], "market": key, "pick": label,
            "probability": round(p, 4), "odds": round(BOOK_MARGIN / p, 2),
        })
    return out


def _pool(all_picks: list[list[dict]], window: tuple[float, float]) -> list[dict]:
    lo, hi = window
    pool = []
    for picks in all_picks:
        ok = sorted((p for p in picks if lo <= p["odds"] <= hi), key=lambda p: -p["probability"])
        pool.extend(ok[:2])
    pool.sort(key=lambda p: -p["probability"])
    return pool[:POOL_SIZE]


def best_combo(pool: list[dict], lo: float, hi: float) -> tuple[float, float, tuple] | None:
    """Combinaison (un seul pari par match) dont la cote totale est dans [lo, hi] et la probabilité maximale."""
    best = None
    for r in range(1, MAX_LEGS + 1):
        for combo in itertools.combinations(pool, r):
            if len({c["match_id"] for c in combo}) < r:
                continue
            odds = math.prod(c["odds"] for c in combo)
            if not lo <= odds <= hi:
                continue
            prob = math.prod(c["probability"] for c in combo)
            if best is None or prob > best[0]:
                best = (prob, odds, combo)
    return best


def build_coupons(matches: list[dict], teams: dict) -> list[dict]:
    all_picks = [match_picks(m, teams) for m in matches]
    coupons = []
    for key, label, lo, hi, window in CATEGORIES:
        base = {"key": key, "label": label, "min": lo, "max": hi}
        found = best_combo(_pool(all_picks, window), lo, hi)
        if not found:
            coupons.append({**base, "empty": True})
            continue
        prob, odds, legs = found
        coupons.append({**base, "empty": False, "total_odds": round(odds, 2),
                        "probability": round(prob, 4), "legs": list(legs)})
    return coupons


def _quality(team: dict) -> float:
    return team["attack"] / max(team["defense"], 0.3)


def pick_big_matches(matches: list[dict], teams: dict, n: int = 3) -> list[dict]:
    def importance(m):
        bonus = 1.0 if m.get("league_id") in CHAMPIONS_IDS else 0.0
        return _quality(teams[m["home"]]) + _quality(teams[m["away"]]) + bonus
    return sorted(matches, key=importance, reverse=True)[:n]


def template_analysis(match: dict, lh: float, la: float, probs: dict, score: str) -> str:
    home, away = match["home"], match["away"]
    if probs["1"] >= probs["2"]:
        fav, p_fav, other, p_other = home, probs["1"], away, probs["2"]
    else:
        fav, p_fav, other, p_other = away, probs["2"], home, probs["1"]
    return (
        f"{fav} part favori avec environ {round(p_fav * 100)} % de chances de victoire, contre "
        f"{round(probs['X'] * 100)} % pour le nul et {round(p_other * 100)} % pour {other}. "
        f"Le modèle attend {lh:.2f} but pour {home} et {la:.2f} pour {away} ; le score le plus probable est {score}. "
        f"Plus de 2,5 buts : {round(probs['over_2_5'] * 100)} % ; les deux équipes marquent : {round(probs['btts_yes'] * 100)} %. "
        "Cette lecture repose uniquement sur les statistiques de buts de la saison : "
        "compositions, blessures et enjeu du match ne sont pas pris en compte."
    )


def claude_analysis(match: dict, facts: str) -> str | None:
    """Analyse rédigée par Claude (nécessite ANTHROPIC_API_KEY). Renvoie None en cas d'échec."""
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return None
    body = {
        "model": os.getenv("ANALYSIS_MODEL", "claude-sonnet-5-5"),
        "max_tokens": 500,
        "system": (
            "Tu es analyste football. Rédige en français une analyse de 4 à 5 phrases à partir UNIQUEMENT "
            "des chiffres fournis. N'invente ni blessures, ni compositions, ni résultats récents. "
            "Dis clairement que ce sont des probabilités, jamais des certitudes. Pas de promesse de gain."
        ),
        "messages": [{"role": "user", "content": facts}],
    }
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(body).encode(),
        headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            data = json.load(r)
        return data["content"][0]["text"].strip() or None
    except Exception as e:
        print(f"[coupons] analyse Claude indisponible pour {match['home']} - {match['away']} : {e}")
        return None


def analyse_match(match: dict, teams: dict) -> dict:
    lh, la = expected_goals(teams[match["home"]], teams[match["away"]])
    probs = market_probs(lh, la)
    score = summarize(score_matrix(lh, la))["most_likely_score"]
    facts = (
        f"Match : {match['home']} (domicile) contre {match['away']} ({match['league']}).\n"
        f"Buts attendus : {lh:.2f} pour {match['home']}, {la:.2f} pour {match['away']}.\n"
        f"Probabilités : victoire {match['home']} {probs['1']:.0%}, nul {probs['X']:.0%}, victoire {match['away']} {probs['2']:.0%}.\n"
        f"Plus de 2,5 buts : {probs['over_2_5']:.0%} ; les deux marquent : {probs['btts_yes']:.0%} ; score le plus probable : {score}."
    )
    text = claude_analysis(match, facts)
    return {
        "match_id": match["id"], "league": match["league"], "home": match["home"], "away": match["away"],
        "home_win": round(probs["1"], 4), "draw": round(probs["X"], 4), "away_win": round(probs["2"], 4),
        "over_2_5": round(probs["over_2_5"], 4), "btts": round(probs["btts_yes"], 4),
        "most_likely_score": score, "xg_home": round(lh, 2), "xg_away": round(la, 2),
        "analysis": text or template_analysis(match, lh, la, probs, score),
        "analysis_source": "claude" if text else "modele",
    }


def build_daily(matches: list[dict], teams: dict, statuses: set[str]) -> dict:
    usable = [m for m in matches if m.get("reliable", True) and m["status"] in statuses]
    now = datetime.now(timezone.utc)
    return {
        "date": now.strftime("%Y-%m-%d"),
        "generated_at": now.isoformat(timespec="seconds"),
        "matches_used": len(usable),
        "coupons": build_coupons(usable, teams) if usable else [],
        "big_matches": [analyse_match(m, teams) for m in pick_big_matches(usable, teams)],
        "note": (
            "Cotes estimées par le modèle (les cotes réelles des opérateurs varient). "
            "Les probabilités combinées supposent des matchs indépendants. Aucun gain n'est garanti : "
            "ne misez que ce que vous pouvez perdre. Réservé aux 18 ans et plus."
        ),
    }
