"""Source de données réelle : football-data.org (API v4).

La clé est lue dans la variable d'environnement FOOTBALL_DATA_KEY (jamais écrite dans le code).
L'offre gratuite donne les 12 compétitions ci-dessous, la saison en cours, 10 requêtes par minute.
Les scores en direct y sont retardés : les minutes affichées en direct sont des estimations.

Réglages (variables d'environnement, tous optionnels) :
- COMPETITIONS : codes de compétitions séparés par des virgules (par défaut : les 12 de l'offre gratuite)
- POLL_SECONDS : intervalle d'actualisation des matchs (1200 s par défaut)

Les cartons rouges ne sont pas récupérés (comptés à 0).
"""
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = "https://api.football-data.org/v4"

# Ligue des champions en premier : les championnats nationaux, traités ensuite, écrasent ses valeurs
# (la force d'une équipe est calculée par rapport à la moyenne de SON championnat).
DEFAULT_COMPETITIONS = ["CL", "PL", "PD", "BL1", "SA", "FL1", "DED", "PPL", "ELC", "BSA", "WC", "EC"]
COMPETITIONS = [c.strip().upper() for c in os.getenv("COMPETITIONS", "").split(",") if c.strip()] or DEFAULT_COMPETITIONS

SHRINK = 5  # plus il est grand, plus les forces d'équipe sont ramenées vers la moyenne
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "300"))  # actualisation hors match
LIVE_POLL_SECONDS = int(os.getenv("LIVE_POLL_SECONDS", "60"))  # actualisation pendant un match en cours ou imminent
FORM_GAMES = 6  # nombre de derniers matchs analysés par équipe

LIVE_STATUS = {"IN_PLAY"}
PAUSED_STATUS = {"PAUSED"}
FINISHED_STATUS = {"FINISHED", "AWARDED"}
SCHEDULED_STATUS = {"SCHEDULED", "TIMED"}

STRENGTH_SEASON: int | None = None  # année de début de la saison utilisée pour les forces d'équipe

_errors: dict[str, str | None] = {"strengths": None, "matches": None}
_blocked_until = 0.0


class FatalError(Exception):
    """Erreur qui rend inutile toute nouvelle requête (clé absente ou refusée)."""


def data_error() -> str | None:
    """Message court en français décrivant le problème de données en cours, sinon None."""
    return _errors["strengths"] or _errors["matches"]


def quota_low() -> bool:
    return time.time() < _blocked_until


def _key() -> str:
    key = os.getenv("FOOTBALL_DATA_KEY", "").strip().strip("\"'").strip()  # tolère espaces et guillemets collés par erreur
    if not key:
        raise FatalError("clé football-data.org absente (variable FOOTBALL_DATA_KEY)")
    return key


def _get(path: str, _retry: bool = True) -> dict:
    global _blocked_until
    req = urllib.request.Request(BASE + path, headers={"X-Auth-Token": _key()})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.load(r)
            avail = r.headers.get("X-Requests-Available-Minute")
    except urllib.error.HTTPError as e:
        try:
            msg = json.load(e).get("message", "")
        except Exception:
            msg = ""
        if e.code == 429:
            if _retry:
                time.sleep(62)
                return _get(path, _retry=False)
            _blocked_until = time.time() + 120
            raise RuntimeError("limite de requêtes atteinte") from e
        if "token" in msg.lower() or e.code == 401:
            raise FatalError("clé football-data.org refusée") from e
        raise RuntimeError(f"HTTP {e.code} : {msg}") from e
    # Ménage la limite par minute : si elle est épuisée, on attend le renouvellement
    if avail is not None and avail.isdigit() and int(avail) <= 0:
        time.sleep(62)
    return data


def diagnose() -> dict:
    """Test simple de la clé (sans la révéler) : renvoie la réponse brute de football-data.org."""
    raw = os.getenv("FOOTBALL_DATA_KEY", "")
    info = {"key_length": len(raw.strip()), "has_spaces_or_quotes": raw != raw.strip().strip("\"'").strip() or " " in raw.strip(),
            "key_start": raw.strip()[:3] + "…" if raw.strip() else ""}
    try:
        req = urllib.request.Request(BASE + "/competitions/PL", headers={"X-Auth-Token": _key()})
        with urllib.request.urlopen(req, timeout=20) as r:
            info.update(http_status=r.status, ok=True)
    except urllib.error.HTTPError as e:
        try:
            msg = json.load(e).get("message", "")
        except Exception:
            msg = ""
        info.update(http_status=e.code, ok=False, api_message=msg)
    except Exception as e:
        info.update(ok=False, error=str(e))
    return info


def team_name(t: dict) -> str:
    return t.get("shortName") or t.get("name") or "?"


def strengths_from_table(table: list[dict]) -> dict[str, dict]:
    """Calcule attaque/défense (1.0 = moyenne du championnat) depuis un classement football-data.org."""
    played = sum(t.get("playedGames") or 0 for t in table)
    if played == 0:
        return {}
    avg = sum(t.get("goalsFor") or 0 for t in table) / played  # played compte chaque match 2 fois
    out = {}
    for t in table:
        n = t.get("playedGames") or 0
        if n == 0 or avg == 0:
            continue
        w = n / (n + SHRINK)  # peu de matchs joués → on reste proche de 1.0
        att = ((t.get("goalsFor") or 0) / n) / avg
        dfn = ((t.get("goalsAgainst") or 0) / n) / avg
        out[team_name(t["team"])] = {
            "attack": round(1 + w * (att - 1), 3),
            "defense": round(1 + w * (dfn - 1), 3),
        }
    return out


def parse_standings(data: dict) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for group in data.get("standings", []):
        if group.get("type", "TOTAL") == "TOTAL":
            result.update(strengths_from_table(group.get("table", [])))
    return result


def fetch_strengths() -> dict[str, dict]:
    """Forces d'équipe depuis les classements de la saison en cours."""
    global STRENGTH_SEASON
    result: dict[str, dict] = {}
    season_year = None
    for code in COMPETITIONS:
        try:
            data = _get(f"/competitions/{code}/standings")
        except FatalError as e:
            _errors["strengths"] = f"{e}"
            print(f"[football-data] arrêt : {e}")
            return result
        except Exception as e:
            print(f"[football-data] classement {code} : {e}")
            continue
        got = parse_standings(data)
        if got:
            result.update(got)
            start = (data.get("season") or {}).get("startDate", "")
            if season_year is None and start[:4].isdigit():
                season_year = int(start[:4])
    if result:
        STRENGTH_SEASON = season_year
        _errors["strengths"] = None
        print(f"[football-data] forces d'équipe calculées ({len(result)} équipes, saison {season_year})")
    else:
        _errors["strengths"] = "statistiques des équipes indisponibles (aucun classement reçu)"
        print("[football-data] AUCUN classement récupéré : aucune prédiction fiable ne sera affichée")
    return result


def _estimate_minute(f: dict, status: str, now: datetime) -> int:
    if status in PAUSED_STATUS:
        return 45
    m = f.get("minute")
    if isinstance(m, int):
        return max(1, min(90, m))
    try:
        start = datetime.fromisoformat(f["utcDate"].replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return 1
    elapsed = (now - start).total_seconds() / 60
    est = elapsed if elapsed <= 47 else max(46, elapsed - 15)  # 15 min de mi-temps
    return max(1, min(90, int(est)))


def parse_match(f: dict, now: datetime | None = None) -> dict | None:
    """Convertit un match football-data.org au format interne. Renvoie None pour un match annulé ou reporté."""
    now = now or datetime.now(timezone.utc)
    api_status = f.get("status", "")
    if api_status in LIVE_STATUS or api_status in PAUSED_STATUS:
        status, minute = "live", _estimate_minute(f, api_status, now)
    elif api_status in FINISHED_STATUS:
        status, minute = "finished", 90
    elif api_status in SCHEDULED_STATUS:
        status, minute = "scheduled", 0
    else:
        return None
    comp = f.get("competition", {})
    full = (f.get("score") or {}).get("fullTime") or {}
    return {
        "id": f["id"],
        "league_code": comp.get("code"),
        "league": comp.get("name", "?"),
        "home": team_name(f["homeTeam"]),
        "away": team_name(f["awayTeam"]),
        "minute": minute,
        "score": [full.get("home") or 0, full.get("away") or 0],
        "red": [0, 0],
        "status": status,
        "events": [],
        "kickoff": f.get("utcDate"),
    }


def form_from_matches(finished: list[dict], games: int = FORM_GAMES) -> dict[str, list[dict]]:
    """Derniers résultats de chaque équipe (du plus ancien au plus récent) depuis des matchs terminés."""
    per_team: dict[str, list[dict]] = {}
    seen = set()
    for f in sorted(finished, key=lambda x: x.get("utcDate", "")):
        if f.get("status") not in FINISHED_STATUS or f.get("id") in seen:
            continue
        full = (f.get("score") or {}).get("fullTime") or {}
        gh, ga = full.get("home"), full.get("away")
        if gh is None or ga is None:
            continue
        seen.add(f.get("id"))
        home, away = team_name(f["homeTeam"]), team_name(f["awayTeam"])
        comp = (f.get("competition") or {}).get("code")
        for me, opp, gf, gc, is_home in ((home, away, gh, ga, True), (away, home, ga, gh, False)):
            per_team.setdefault(me, []).append({
                "d": str(f.get("utcDate", ""))[:10], "opp": opp, "home": is_home,
                "gf": gf, "ga": gc, "r": "V" if gf > gc else "N" if gf == gc else "D", "comp": comp,
            })
    return {t: g[-games:] for t, g in per_team.items()}


def form_summary(games: list[dict]) -> dict:
    w = sum(1 for g in games if g["r"] == "V")
    d = sum(1 for g in games if g["r"] == "N")
    return {"n": len(games), "w": w, "d": d, "l": len(games) - w - d,
            "gf": sum(g["gf"] for g in games), "ga": sum(g["ga"] for g in games), "pts": 3 * w + d}


def fetch_form() -> dict[str, list[dict]]:
    """Les 6 derniers matchs de chaque équipe, à partir des matchs terminés de la saison en cours
    (une requête par compétition)."""
    finished: list[dict] = []
    for code in COMPETITIONS:
        try:
            finished.extend(_get(f"/competitions/{code}/matches?status=FINISHED").get("matches", []))
        except FatalError as e:
            print(f"[football-data] arrêt de la forme : {e}")
            break
        except Exception as e:
            print(f"[football-data] forme {code} : {e}")
    result = form_from_matches(finished)
    print(f"[football-data] forme récente calculée pour {len(result)} équipes")
    return result


def fetch_matches() -> list[dict]:
    """Matchs d'aujourd'hui et de demain des compétitions suivies, au format interne de l'application."""
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    after = (now + timedelta(days=2)).strftime("%Y-%m-%d")
    try:
        data = _get(f"/matches?dateFrom={today}&dateTo={after}")
    except FatalError as e:
        _errors["matches"] = f"{e}"
        raise
    except Exception as e:
        _errors["matches"] = f"données de matchs indisponibles ({e})"
        raise
    _errors["matches"] = None
    matches = []
    for f in data.get("matches", []):
        if str(f.get("utcDate", ""))[:10] not in (today, tomorrow):
            continue
        m = parse_match(f, now)
        if m and m["league_code"] in COMPETITIONS:
            matches.append(m)
    matches.sort(key=lambda m: (m["kickoff"] or "", m["league"]))
    return matches
