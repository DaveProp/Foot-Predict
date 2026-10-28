"""Source de données réelle : API-Football (api-sports.io, v3).

La clé est lue dans la variable d'environnement FOOTBALL_API_KEY (jamais écrite dans le code).

Réglages (variables d'environnement, tous optionnels) :
- LEAGUE_IDS    : liste d'identifiants de ligues séparés par des virgules (voir LEAGUES ci-dessous)
- POLL_SECONDS  : intervalle d'actualisation des matchs (1200 s par défaut, adapté à l'offre gratuite ~100 requêtes/jour)
- SEASON        : saison forcée pour les classements (sinon essai automatique de la saison en cours puis des précédentes)

Les cartons rouges ne sont pas récupérés (comptés à 0) pour économiser les requêtes.
"""
import json
import os
import urllib.request
from datetime import datetime, timezone

BASE = "https://v3.football.api-sports.io"

# Championnats suivis par défaut (identifiants API-Football) :
# Premier League, Liga, Serie A, Bundesliga, Ligue 1, Ligue des champions, Europa League,
# Conference League, Primeira Liga, Eredivisie, Pro League belge, Süper Lig, MLS,
# Ligue 1 ivoirienne, Ligue des champions CAF, Coupe de la Confédération CAF
DEFAULT_LEAGUES = [39, 140, 135, 78, 61, 2, 3, 848, 94, 88, 144, 203, 253, 386, 12, 20]
LEAGUES = [int(x) for x in os.getenv("LEAGUE_IDS", "").split(",") if x.strip()] or DEFAULT_LEAGUES

SHRINK = 5  # plus il est grand, plus les forces d'équipe sont ramenées vers la moyenne
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "1200"))

LIVE_STATUS = {"1H", "2H", "ET", "BT", "P", "LIVE", "INT"}
FINISHED_STATUS = {"FT", "AET", "PEN"}

remaining_requests: int | None = None  # quota restant, lu dans les en-têtes de réponse
STRENGTH_SEASON: int | None = None  # saison réellement utilisée pour calculer les forces d'équipe


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


def _standings(league: int, season: int) -> dict[str, dict]:
    data = _get(f"/standings?league={league}&season={season}")
    result: dict[str, dict] = {}
    for group in data["response"][0]["league"]["standings"]:
        result.update(strengths_from_table(group))
    return result


def fetch_strengths() -> dict[str, dict]:
    """Forces d'équipe depuis les classements.

    Essaie d'abord la saison en cours, puis les précédentes (l'offre gratuite refuse souvent les
    saisons récentes). La première saison qui fonctionne est conservée pour toutes les ligues.
    """
    global STRENGTH_SEASON
    result: dict[str, dict] = {}
    base = current_season()
    seasons = [base] if os.getenv("SEASON") else [base, base - 1, base - 2, base - 3]
    working = STRENGTH_SEASON
    for league in LEAGUES:
        if quota_low():
            print("[api-football] quota presque épuisé, arrêt des classements")
            break
        candidates = [working] if working else seasons
        for season in candidates:
            try:
                result.update(_standings(league, season))
                working = season
                break
            except Exception as e:
                print(f"[api-football] classement ligue {league} saison {season} : {e}")
    if working:
        STRENGTH_SEASON = working
        print(f"[api-football] forces d'équipe calculées sur la saison {working} ({len(result)} équipes)")
    else:
        print("[api-football] AUCUN classement récupéré : aucune prédiction fiable ne sera affichée")
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
    league = f["league"]["name"]
    country = f["league"].get("country")
    if country and country != "World":
        league = f"{league} ({country})"
    return {
        "id": f["fixture"]["id"],
        "league_id": f["league"].get("id"),
        "league": league,
        "home": f["teams"]["home"]["name"],
        "away": f["teams"]["away"]["name"],
        "minute": minute,
        "score": [f["goals"]["home"] or 0, f["goals"]["away"] or 0],
        "red": [0, 0],
        "status": status,
        "events": [],
    }


def fetch_matches() -> list[dict]:
    """Matchs du jour des championnats suivis, au format interne de l'application."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    data = _get(f"/fixtures?date={today}")
    matches = [parse_fixture(f) for f in data["response"]]
    matches = [m for m in matches if m["league_id"] in LEAGUES]
    # Les matchs en cours en premier, puis à venir, puis terminés
    matches.sort(key=lambda m: {"live": 0, "scheduled": 1, "finished": 2}[m["status"]])
    return matches
