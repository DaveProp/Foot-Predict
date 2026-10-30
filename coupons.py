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

BIG_COMPETITIONS = {"CL", "WC", "EC"}  # codes football-data.org : Ligue des champions, Coupe du monde, Euro


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


def leg_won(market: str, h: int, a: int) -> bool:
    """Un pari est-il gagné avec le score final h-a (temps réglementaire) ?"""
    g = h + a
    return {
        "1": h > a, "2": a > h, "1X": h >= a, "X2": a >= h, "12": h != a,
        "over_1_5": g >= 2, "over_2_5": g >= 3, "under_3_5": g <= 3,
        "btts_yes": h > 0 and a > 0, "btts_no": h == 0 or a == 0,
    }[market]


def settle(d: dict, by_id: dict) -> None:
    """Marque chaque sélection gagnée/perdue dès que son match est terminé, puis le coupon :
    gagné si tout est gagné, perdu dès qu'une sélection est perdue, sinon en cours."""
    for c in d.get("coupons", []):
        if c.get("empty"):
            continue
        for leg in c["legs"]:
            if leg.get("result") in ("won", "lost"):
                continue
            m = by_id.get(leg["match_id"])
            sc = m.get("score") if m else None
            if m and m["status"] == "finished" and sc and None not in sc:
                leg["final"] = f"{sc[0]}-{sc[1]}"
                leg["result"] = "won" if leg_won(leg["market"], sc[0], sc[1]) else "lost"
        res = [leg.get("result") for leg in c["legs"]]
        c["status"] = "lost" if "lost" in res else "won" if all(r == "won" for r in res) else "pending"


def _quality(team: dict) -> float:
    return team["attack"] / max(team["defense"], 0.3)


def pick_big_matches(matches: list[dict], teams: dict, n: int = 3) -> list[dict]:
    def importance(m):
        bonus = 1.0 if m.get("league_code") in BIG_COMPETITIONS else 0.0
        return _quality(teams[m["home"]]) + _quality(teams[m["away"]]) + bonus
    return sorted(matches, key=importance, reverse=True)[:n]


def _verdict(p_fav: float) -> str:
    if p_fav >= 0.60:
        return "net favori"
    if p_fav >= 0.48:
        return "favori"
    if p_fav >= 0.40:
        return "léger favori"
    return "à peine favori"


def _de(name: str) -> str:
    return ("d'" if name[:1].lower() in "aeiouyhéèê" else "de ") + name


def _fr(x: float, nd: int = 2) -> str:
    return f"{x:.{nd}f}".replace(".", ",")


def _form_line(match: dict) -> str:
    """Forme récente comparée : qui arrive en meilleure dynamique (points sur les 6 derniers matchs)."""
    form = match.get("form")
    if not form:
        return ""
    h, a = form["home"]["summary"], form["away"]["summary"]
    if not h["n"] or not a["n"]:
        return ""
    ph, pa = h["pts"] / (3 * h["n"]), a["pts"] / (3 * a["n"])
    line = (f"Sur ses {h['n']} derniers matchs, {match['home']} a pris {h['pts']} points sur {3 * h['n']} "
            f"({h['gf']} buts marqués, {h['ga']} encaissés) ; {match['away']} en a pris {a['pts']} sur {3 * a['n']} "
            f"({a['gf']} marqués, {a['ga']} encaissés). ")
    if abs(ph - pa) >= 0.25:
        better = match["home"] if ph > pa else match["away"]
        line += f"La dynamique récente est nettement en faveur {_de(better)}. "
    else:
        line += "Les deux équipes arrivent avec une dynamique comparable. "
    return line


def _markets_line(probs: dict) -> str:
    """Les deux marchés les plus probables parmi ceux qui ne sont pas triviaux."""
    options = [("plus de 1,5 but", probs["over_1_5"]), ("plus de 2,5 buts", probs["over_2_5"]),
               ("moins de 3,5 buts", probs["under_3_5"]), ("les deux équipes marquent", probs["btts_yes"]),
               ("une seule équipe marque ou aucune", probs["btts_no"])]
    options = sorted((o for o in options if 0.55 <= o[1] <= 0.90), key=lambda o: -o[1])[:2]
    if not options:
        return ""
    return "Marchés les plus probables selon le modèle : " + " ; ".join(f"{n} ({round(p * 100)} %)" for n, p in options) + ". "


def template_analysis(match: dict, lh: float, la: float, probs: dict, score: str) -> str:
    home, away = match["home"], match["away"]
    if probs["1"] >= probs["2"]:
        fav, p_fav, other, p_other = home, probs["1"], away, probs["2"]
    else:
        fav, p_fav, other, p_other = away, probs["2"], home, probs["1"]
    balanced = abs(probs["1"] - probs["2"]) < 0.08
    head = (
        f"Match équilibré : {home} {round(probs['1'] * 100)} %, nul {round(probs['X'] * 100)} %, {away} {round(probs['2'] * 100)} %. "
        if balanced else
        f"{fav} est {_verdict(p_fav)} avec {round(p_fav * 100)} % de chances de victoire, contre {round(probs['X'] * 100)} % "
        f"pour le nul et {round(p_other * 100)} % pour {other}. "
    )
    goals = lh + la
    tempo = "un match plutôt ouvert" if goals >= 2.9 else "un match plutôt fermé" if goals <= 2.2 else "un volume de buts moyen"
    body = (
        f"Le modèle attend {_fr(lh)} but pour {home} et {_fr(la)} pour {away} (soit {_fr(goals, 1)} au total : {tempo}) ; "
        f"le score le plus probable est {score}. "
    )
    return (
        head + body + _form_line(match) + _markets_line(probs) +
        "Ces chiffres sont des probabilités calculées avant le coup d'envoi (statistiques de la saison et forme récente) : "
        "compositions, blessures et enjeu ne sont pas pris en compte, et rien n'est jamais certain."
    )


def claude_analysis(match: dict, facts: str) -> str | None:
    """Analyse rédigée par Claude (nécessite ANTHROPIC_API_KEY). Renvoie None en cas d'échec."""
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return None
    body = {
        "model": os.getenv("ANALYSIS_MODEL", "claude-sonnet-5-5"),
        "max_tokens": 800,
        "system": (
            "Tu es un analyste football rigoureux. Rédige en français une analyse de 6 à 8 phrases, structurée en trois "
            "temps : (1) la lecture du match (favori, équilibre, buts attendus, score probable) ; (2) la forme récente "
            "des deux équipes et ce qu'elle change ; (3) les marchés les plus solides et les points d'incertitude. "
            "Appuie-toi UNIQUEMENT sur les chiffres fournis : n'invente ni blessures, ni compositions, ni résultats, ni "
            "enjeux. Compare, interprète, ne te contente pas de recopier les chiffres. Dis clairement que ce sont des "
            "probabilités, jamais des certitudes, et ne promets aucun gain."
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


def form_text(match: dict) -> str:
    """Résumé en une phrase des 6 derniers matchs de chaque équipe (vide si indisponible)."""
    form = match.get("form")
    if not form:
        return ""
    parts = []
    for side in ("home", "away"):
        s = form[side]["summary"]
        parts.append(
            f"{match[side]} : {s['w']} victoire(s), {s['d']} nul(s), {s['l']} défaite(s) sur ses {s['n']} derniers matchs "
            f"({s['gf']} buts marqués, {s['ga']} encaissés)"
        )
    return " ; ".join(parts) + "."


def analyse_match(match: dict, teams: dict) -> dict:
    lh, la = expected_goals(teams[match["home"]], teams[match["away"]])
    probs = market_probs(lh, la)
    score = summarize(score_matrix(lh, la))["most_likely_score"]
    recent = form_text(match)
    facts = (
        f"Match : {match['home']} (domicile) contre {match['away']} ({match['league']}).\n"
        f"Buts attendus : {lh:.2f} pour {match['home']}, {la:.2f} pour {match['away']}.\n"
        f"Probabilités : victoire {match['home']} {probs['1']:.0%}, nul {probs['X']:.0%}, victoire {match['away']} {probs['2']:.0%}.\n"
        f"Plus de 1,5 but : {probs['over_1_5']:.0%} ; plus de 2,5 buts : {probs['over_2_5']:.0%} ; moins de 3,5 buts : {probs['under_3_5']:.0%} ; "
        f"les deux marquent : {probs['btts_yes']:.0%} ; score le plus probable : {score}."
        + (f"\nForme récente : {recent}" if recent else "")
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
