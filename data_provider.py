"""Source de données réelle : API-Football (api-sports.io, v3).

La clé est lue dans la variable d'environnement FOOTBALL_API_KEY (jamais écrite dans le code).

ATTENTION aux quotas : l'offre gratuite d'API-Football est limitée (environ 100 requêtes par jour,
et l'accès aux saisons récentes peut être restreint selon l'offre). C'est pourquoi l'intervalle
d'actualisation est réglable (POLL_SECONDS, 900 s par défaut) et que la saison est réglable (SEASON).
Avec une offre payante, mettre par exemple POLL_SECONDS=30.
Les cartons rouges ne sont pas récupérés (comptés à 0) pour économiser les requêtes.
"""
import json
import os
import urllib.request
from datetime import datetime, timezone

BASE = "https://v3.football.api-sports.io"
# Identifiants de ligues API-Football : Premier League, Liga, Serie A, Bundesliga, Ligue 1,
# Ligue des champions, Ligue 1 ivoirienne
LEAGUES = [39, 140, 135, 78, 61, 2, 386]
SHRINK = 5  # plus il est grand, plus les forces d'équipe sont ramenées vers la moyenne
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "900"))

LIVE_STATUS = {"1H", "2H", "ET", "BT", "P", "LIVE", "INT"}
FINISHED_STATUS = {"FT", "AET", "PEN"}

remaining_requests: int | None = None  # quota restant, lu dans les en-têtes de réponse


def current_season() -> int:
    if os.getenv("SEASON"):
        return int(os.environ["SEASON"])
    now = datetime.now(timezone.utc)
    return now.year if now.month >= 8 else now.year - 1


def _get(path: str) -> dict:
    global remaining_requests
    req = urllib.request.Request(
        BASE + path, headers={"x-apisports-key": os.environ["FOOTBALL_API_KEY"]}
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        left = r.headers.get("x-ratelimit-requests-remaining")
        if left is not None:
            remaining_requests = int(left)
        data = json.load(r)
    errors = data.get("errors")
    if errors:  # l'API renvoie les erreurs (clé, quota, saison non autorisée) dans ce champ
        raise RuntimeError(f"API-Football : {errors}")
    return data


def quota_low() -> bool:
    return remaining_requests is not None and remaining_requests < 5


def strengths_from_table(table: list[dict]) -> dict[str, dict]:
    """Calcule attaque/défense (1.0 = moyenne du championnat) depuis un classement API-Football."""
    played = sum(t["all"]["played"] for t in table)
    if played == 0:
        return {}
    goals_for = sum(t["all"]["goals"]["for"] for t in table)
    avg = goals_for / played  # buts par équipe et par match (played compte chaque match 2 fois)
    out = {}
    for t in table:
        n = t["all"]["played"]
        if n == 0 or avg == 0:
            continue
        w = n / (n + SHRINK)  # peu de matchs joués → on reste proche de 1.0
        att = (t["all"]["goals"]["for"] / n) / avg
        dfn = (t["all"]["goals"]["against"] / n) / avg
        out[t["team"]["name"]] = {
            "attack": round(1 + w * (att - 1), 3),
            "defense": round(1 + w * (dfn - 1), 3),
        }
    return out


def fetch_strengths() -> dict[str, dict]:
    result: dict[str, dict] = {}
    season = current_season()
    for league in LEAGUES:
        if quota_low():
            print("[api-football] quota presque épuisé, arrêt des classements")
            break
        try:
            data = _get(f"/standings?league={league}&season={season}")
            for group in data["response"][0]["league"]["standings"]:
                result.update(strengths_from_table(group))
        except Exception as e:  # une ligue indisponible ne bloque pas les autres
            print(f"[api-football] classement ligue {league} : {e}")
    return result


def parse_fixture(f: dict) -> dict:
    short = f["fixture"]["status"]["short"]
    elapsed = f["fixture"]["status"].get("elapsed") or 0
    if short == "HT":
        status, minute = "live", 45
    elif short in LIVE_STATUS:
        status, minute = "live", min(90, elapsed)
    elif short in FINISHED_STATUS:
        status, minute = "finished", 90
    else:
        status, minute = "scheduled", 0
    return {
        "id": f["fixture"]["id"],
        "league": f["league"]["name"],
        "home": f["teams"]["home"]["name"],
        "away": f["teams"]["away"]["name"],
        "minute": minute,
        "score": [f["goals"]["home"] or 0, f["goals"]["away"] or 0],
        "red": [0, 0],
        "status": status,
        "events": [],
    }


def fetch_matches() -> list[dict]:
    """Matchs du jour (toutes ligues), au format interne de l'application."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    data = _get(f"/fixtures?date={today}")
    matches = [parse_fixture(f) for f in data["response"]]
    # Les matchs en cours en premier
    matches.sort(key=lambda m: {"live": 0, "scheduled": 1, "finished": 2}[m["status"]])
    return matches
