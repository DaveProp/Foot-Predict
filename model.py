"""Modèle de prédiction football : Poisson bivarié (avant-match) + mise à jour en direct.

Les probabilités sont des estimations statistiques, jamais des certitudes.
"""
import math

MAX_GOALS = 10
HOME_ADVANTAGE = 1.10
LEAGUE_AVG_GOALS = 1.35  # buts par équipe et par match (moyenne de référence)


def poisson_pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * lam**k / math.factorial(k)


def expected_goals(home: dict, away: dict) -> tuple[float, float]:
    """Calcule les buts attendus à partir des forces attaque/défense (1.0 = moyenne)."""
    lam_home = LEAGUE_AVG_GOALS * home["attack"] * away["defense"] * HOME_ADVANTAGE
    lam_away = LEAGUE_AVG_GOALS * away["attack"] * home["defense"]
    return lam_home, lam_away


def score_matrix(lam_home: float, lam_away: float, rho: float = -0.05) -> list[list[float]]:
    """Matrice de probabilités des scores finaux, avec correction Dixon-Coles sur les petits scores."""
    m = [
        [poisson_pmf(i, lam_home) * poisson_pmf(j, lam_away) for j in range(MAX_GOALS + 1)]
        for i in range(MAX_GOALS + 1)
    ]
    m[0][0] *= max(0.0, 1 - lam_home * lam_away * rho)
    m[0][1] *= max(0.0, 1 + lam_home * rho)
    m[1][0] *= max(0.0, 1 + lam_away * rho)
    m[1][1] *= max(0.0, 1 - rho)
    total = sum(sum(r) for r in m)
    return [[p / total for p in r] for r in m]


def summarize(matrix: list[list[float]], cur_home: int = 0, cur_away: int = 0) -> dict:
    """Transforme la matrice des buts restants en probabilités 1X2, over 2.5 et BTTS,
    en tenant compte du score actuel."""
    home_win = draw = away_win = over25 = btts = 0.0
    best = (0.0, 0, 0)
    for i, row in enumerate(matrix):
        for j, p in enumerate(row):
            fh, fa = cur_home + i, cur_away + j
            if fh > fa:
                home_win += p
            elif fh == fa:
                draw += p
            else:
                away_win += p
            if fh + fa > 2:
                over25 += p
            if fh > 0 and fa > 0:
                btts += p
            if p > best[0]:
                best = (p, fh, fa)
    return {
        "home_win": round(home_win, 4),
        "draw": round(draw, 4),
        "away_win": round(away_win, 4),
        "over_2_5": round(over25, 4),
        "btts": round(btts, 4),
        "most_likely_score": f"{best[1]}-{best[2]}",
    }


def predict_prematch(home: dict, away: dict) -> dict:
    lh, la = expected_goals(home, away)
    result = summarize(score_matrix(lh, la))
    result["xg_home"], result["xg_away"] = round(lh, 2), round(la, 2)
    return result


def predict_live(home: dict, away: dict, minute: int, score: tuple[int, int],
                 red_home: int = 0, red_away: int = 0) -> dict:
    """Recalcule les probabilités pendant le match.

    - Les buts attendus restants sont proportionnels au temps restant.
    - Un carton rouge réduit l'attaque de l'équipe réduite à 10 et affaiblit sa défense.
    """
    lh, la = expected_goals(home, away)
    remaining = max(0, 90 - minute) / 90
    lh_rem, la_rem = lh * remaining, la * remaining
    # Effet des cartons rouges (approximation statistique simple)
    lh_rem *= (0.80 ** red_home) * (1.25 ** red_away)
    la_rem *= (0.80 ** red_away) * (1.25 ** red_home)
    # Une équipe qui perd pousse davantage vers la fin du match
    diff = score[0] - score[1]
    if diff < 0:
        lh_rem *= 1.08
    elif diff > 0:
        la_rem *= 1.08
    result = summarize(score_matrix(lh_rem, la_rem), score[0], score[1])
    result["xg_home"], result["xg_away"] = round(lh_rem, 2), round(la_rem, 2)
    return result
