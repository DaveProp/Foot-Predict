"""API FastAPI : prédictions, flux en direct (WebSocket), accès Premium (Wave) et coupons du jour.

En mode démo (sans FOOTBALL_DATA_KEY), les matchs « en direct » sont simulés.
En mode réel, les données viennent de football-data.org et les coupons sont régénérés chaque jour à 00h (UTC = Abidjan).

Variables d'environnement utiles : FOOTBALL_DATA_KEY (football-data.org), ACCESS_SECRET, ADMIN_KEY, WAVE_URL, PLAN_LABEL,
WHATSAPP_NUMBER, ANTHROPIC_API_KEY (analyses rédigées par Claude, facultatif).
"""
import asyncio
import hmac
import os
import random
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

import access
import coupons
from model import expected_goals, predict_live, predict_prematch

WAVE_BASE = os.getenv("WAVE_URL", "https://pay.wave.com/m/M_ci_fAtDWzspJqhS/c/ci/")
# Formules d'abonnement : le montant est pré-rempli dans le lien Wave
PLANS = [
    {"key": "semaine", "label": "1 semaine", "days": 7, "amount": 3800, "url": f"{WAVE_BASE}?amount=3800"},
    {"key": "mois", "label": "1 mois", "days": 30, "amount": 12000, "url": f"{WAVE_BASE}?amount=12000"},
]
PLAN_DAYS = {p["key"]: p["days"] for p in PLANS}
WHATSAPP_NUMBER = "".join(ch for ch in os.getenv("WHATSAPP_NUMBER", "") if ch.isdigit())
ADMIN_KEY = os.getenv("ADMIN_KEY", "")

# Forces attaque/défense (1.0 = moyenne). Valeurs de démonstration.
TEAMS = {
    "PSG": {"attack": 1.55, "defense": 0.70},
    "Marseille": {"attack": 1.25, "defense": 0.95},
    "Lyon": {"attack": 1.15, "defense": 1.00},
    "Lille": {"attack": 1.10, "defense": 0.85},
    "Real Madrid": {"attack": 1.60, "defense": 0.75},
    "Barcelone": {"attack": 1.50, "defense": 0.85},
    "Manchester City": {"attack": 1.70, "defense": 0.65},
    "Arsenal": {"attack": 1.45, "defense": 0.70},
    "Asec Mimosas": {"attack": 1.05, "defense": 0.90},
    "Africa Sports": {"attack": 0.90, "defense": 1.05},
}

FIXTURES = [
    ("PSG", "Marseille", "Ligue 1"),
    ("Lyon", "Lille", "Ligue 1"),
    ("Real Madrid", "Barcelone", "La Liga"),
    ("Manchester City", "Arsenal", "Premier League"),
    ("Asec Mimosas", "Africa Sports", "Ligue 1 Ivoirienne"),
]

matches: dict[int, dict] = {}
clients: dict[WebSocket, str] = {}  # connexion -> code d'accès présenté (vide = gratuit)
daily: dict = {"date": None, "coupons": [], "big_matches": [], "note": "", "ts": 0.0}
previous: dict = {}  # coupons de la veille, conservés avec leurs résultats
wake = asyncio.Event()  # réveille real_loop à minuit
wake_strengths = asyncio.Event()  # réveille strengths_loop à minuit


def build_matches() -> None:
    history_store.clear()
    for i, (h, a, league) in enumerate(FIXTURES, start=1):
        matches[i] = {
            "id": i, "league": league, "home": h, "away": a,
            "minute": random.randint(0, 5), "score": [0, 0],
            "red": [0, 0], "status": "live", "events": [], "reliable": True,
        }
        refresh(matches[i])


history_store: dict[int, list] = {}  # évolution des probabilités minute par minute, par match


def refresh(m: dict) -> None:
    home, away = TEAMS[m["home"]], TEAMS[m["away"]]
    m["prematch"] = predict_prematch(home, away)
    m["live"] = predict_live(home, away, m["minute"], tuple(m["score"]), *m["red"])
    hist = history_store.setdefault(m["id"], [])
    if not hist or hist[-1][0] != m["minute"]:
        hist.append([m["minute"], m["live"]["home_win"], m["live"]["draw"], m["live"]["away_win"]])
    m["history"] = hist


def simulate_tick(m: dict) -> None:
    """Fait avancer un match simulé d'une minute (buts et cartons selon les xG)."""
    if m["status"] == "finished":
        return
    m["minute"] += 1
    home, away = TEAMS[m["home"]], TEAMS[m["away"]]
    lh, la = expected_goals(home, away)
    if random.random() < lh / 90:
        m["score"][0] += 1
        m["events"].append({"minute": m["minute"], "type": "but", "team": m["home"]})
    if random.random() < la / 90:
        m["score"][1] += 1
        m["events"].append({"minute": m["minute"], "type": "but", "team": m["away"]})
    if random.random() < 0.003:
        side = random.randint(0, 1)
        m["red"][side] += 1
        m["events"].append({"minute": m["minute"], "type": "carton rouge",
                            "team": m["home"] if side == 0 else m["away"]})
    if m["minute"] >= 90:
        m["status"] = "finished"
    refresh(m)


def mode() -> str:
    return "reel" if os.getenv("FOOTBALL_DATA_KEY") else "demo"


def season():
    if not os.getenv("FOOTBALL_DATA_KEY"):
        return None
    import data_provider
    return data_provider.STRENGTH_SEASON


def data_error():
    if not os.getenv("FOOTBALL_DATA_KEY"):
        return None
    import data_provider
    return data_provider.data_error()


# --- Accès Premium -------------------------------------------------------------------------

FREE_FIELDS = ("id", "league", "home", "away", "minute", "score", "status", "reliable", "events", "kickoff")


def public_view(m: dict, premium: bool) -> dict:
    """Les abonnés voient tout ; les non-abonnés voient les matchs et les scores, sans prédictions."""
    if premium:
        return m
    view = {k: m[k] for k in FREE_FIELDS if k in m}
    view["locked"] = True
    return view


def payload_for(code: str) -> dict:
    expires = access.check_code(code)
    premium = expires is not None
    return {
        "type": "update", "mode": mode(), "season": season(),
        "premium": premium, "expires": expires.isoformat() if expires else None,
        "data_error": data_error(),
        "matches": [public_view(m, premium) for m in list(matches.values())],
    }


def settle_all() -> None:
    by_id = {m["id"]: m for m in matches.values()}
    coupons.settle(daily, by_id)
    if previous:
        coupons.settle(previous, by_id)


async def broadcast() -> None:
    settle_all()
    for ws, code in list(clients.items()):
        try:
            await ws.send_json(payload_for(code))
        except Exception:
            clients.pop(ws, None)


# --- Coupons du jour (régénérés à 00h) -----------------------------------------------------


def today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


async def ensure_daily() -> None:
    """Construit les coupons et analyses du jour s'ils ne sont pas déjà faits (ou si la tentative était vide)."""
    global daily, previous
    if daily["date"] == today_str() and not daily.get("stale") and (daily["coupons"] or time.time() - daily["ts"] < 1800):
        return
    # Tous les matchs du jour comptent, même déjà commencés : les coupons ne dépendent que des statistiques des équipes
    # (pas du score), donc refaits après un redémarrage en soirée, ils redonnent les mêmes que ceux du matin.
    statuses = {"scheduled", "live", "finished"}
    # Les coupons du jour ne portent que sur les matchs du jour (pas ceux de demain)
    snapshot = [dict(m) for m in matches.values() if not m.get("kickoff") or m["kickoff"][:10] == today_str()]
    try:
        result = await asyncio.to_thread(coupons.build_daily, snapshot, dict(TEAMS), statuses)
    except Exception as e:
        print(f"[daily] erreur : {e}")
        return
    result["ts"] = time.time()
    if daily.get("date") and daily["date"] != result["date"] and daily["coupons"]:
        settle_all()
        previous = daily  # la veille : on garde ses résultats visibles
    daily = result
    print(f"[daily] {result['date']} : {len(result['coupons'])} coupons, {len(result['big_matches'])} grands matchs")


async def midnight_loop() -> None:
    """Réveille la mise à jour des données chaque jour à 00h00 (heure d'Abidjan = UTC)."""
    while True:
        now = datetime.now(timezone.utc)
        nxt = (now + timedelta(days=1)).replace(hour=0, minute=0, second=10, microsecond=0)
        await asyncio.sleep((nxt - now).total_seconds())
        wake.set()
        wake_strengths.set()


async def ticker() -> None:
    while True:
        await asyncio.sleep(2)  # 1 minute de match simulée toutes les 2 secondes
        for m in matches.values():
            simulate_tick(m)
        if all(m["status"] == "finished" for m in matches.values()):
            await asyncio.sleep(5)
            build_matches()
        await ensure_daily()
        await broadcast()


USE_REAL = bool(os.getenv("FOOTBALL_DATA_KEY"))
DEFAULT_TEAM = {"attack": 1.0, "defense": 1.0}
real_strengths: dict[str, dict] = {}
real_form: dict[str, list] = {}  # 6 derniers matchs de chaque équipe


def attach_form(m: dict) -> None:
    import data_provider
    hf, af = real_form.get(m["home"]), real_form.get(m["away"])
    if hf and af:
        m["form"] = {
            "home": {"games": hf, "summary": data_provider.form_summary(hf)},
            "away": {"games": af, "summary": data_provider.form_summary(af)},
        }
    else:
        m.pop("form", None)


phase: dict[int, dict] = {}  # moments réellement observés : début de chaque mi-temps


def real_minute(m: dict, old: dict | None) -> None:
    """Minute réelle à partir des changements de statut vus pendant nos actualisations (précision ~1 min).
    L'API gratuite ne donne pas la minute et le coup d'envoi réel diffère souvent de l'heure prévue.
    Sans observation fiable (ex. redémarrage en plein match), on garde l'estimation faite depuis l'heure prévue."""
    now = time.time()
    ph = phase.setdefault(m["id"], {})
    prev = old["status"] if old else None
    if m["status"] != "live":
        return
    if m.get("paused"):
        ph["ht"] = True
        return
    if prev == "scheduled" and "h1" not in ph:
        ph["h1"] = now
    if ph.get("ht") and "h2" not in ph:
        if old and old.get("paused"):
            ph["h2"] = now
    if "h2" in ph:
        m["minute"] = min(90, 46 + int((now - ph["h2"]) / 60))
    elif "h1" in ph and not ph.get("ht"):
        m["minute"] = min(45, 1 + int((now - ph["h1"]) / 60))


def apply_matches(fresh: list[dict]) -> None:
    """Met à jour la liste des matchs d'un seul bloc (pas de liste vide visible entre deux actualisations)."""
    new: dict[int, dict] = {}
    for m in fresh:
        for team in (m["home"], m["away"]):
            TEAMS[team] = real_strengths.get(team, TEAMS.get(team, DEFAULT_TEAM))
        # Prédiction fiable seulement si les deux équipes ont de vraies statistiques
        m["reliable"] = m["home"] in real_strengths and m["away"] in real_strengths
        real_minute(m, matches.get(m["id"]))
        refresh(m)
        attach_form(m)
        new[m["id"]] = m
    matches.clear()
    matches.update(new)


def next_poll() -> float:
    """Actualisation rapide pendant un match en cours ou imminent, plus lente le reste du temps."""
    import data_provider
    now = datetime.now(timezone.utc)
    for m in matches.values():
        if m["status"] == "live":
            return data_provider.LIVE_POLL_SECONDS
        if m["status"] == "scheduled" and m.get("kickoff"):
            try:
                start = datetime.fromisoformat(m["kickoff"].replace("Z", "+00:00"))
            except ValueError:
                continue
            if -1800 <= (start - now).total_seconds() <= 900:
                return data_provider.LIVE_POLL_SECONDS
    return data_provider.POLL_SECONDS


async def real_loop() -> None:
    """Mode réel : actualise les matchs d'aujourd'hui et de demain (toutes les minutes en cas de match, sinon POLL_SECONDS)."""
    import data_provider
    while True:
        try:
            if not data_provider.quota_low():
                apply_matches(await asyncio.to_thread(data_provider.fetch_matches))
                await ensure_daily()
                await broadcast()
            else:
                print("[real_loop] limite de requêtes atteinte : pause")
        except Exception as e:
            print(f"[real_loop] erreur : {e}")
        try:
            await asyncio.wait_for(wake.wait(), next_poll())
        except asyncio.TimeoutError:
            pass
        wake.clear()


async def strengths_loop() -> None:
    """Chaque jour (et à minuit) : forces d'équipe et 6 derniers matchs. Tâche lente (limite de 10 requêtes/minute),
    séparée de la boucle des scores pour ne jamais la bloquer."""
    import data_provider
    while True:
        retry = 1800
        try:
            got = await asyncio.to_thread(data_provider.fetch_strengths)
            if got:
                real_strengths.update(got)
                real_form.update(await asyncio.to_thread(data_provider.fetch_form))
                apply_matches(list(matches.values()))
                daily["stale"] = True  # les coupons doivent être refaits avec les vraies statistiques
                await ensure_daily()
                await broadcast()
                retry = 86400
        except Exception as e:
            print(f"[strengths_loop] erreur : {e}")
        try:
            await asyncio.wait_for(wake_strengths.wait(), retry)  # minuit ou nouvel essai dans 30 min
        except asyncio.TimeoutError:
            pass
        wake_strengths.clear()


@asynccontextmanager
async def lifespan(_: FastAPI):
    if not access.SECRET_IS_FIXED:
        print("ATTENTION : ACCESS_SECRET non défini, les codes d'accès seront invalidés à chaque redémarrage")
    if not ADMIN_KEY:
        print("ATTENTION : ADMIN_KEY non défini, impossible de créer des codes d'accès")
    if USE_REAL:
        print("Mode RÉEL : données football-data.org")
        tasks = [asyncio.create_task(real_loop()), asyncio.create_task(strengths_loop()),
                 asyncio.create_task(midnight_loop())]
    else:
        print("Mode DÉMO : matchs simulés (définir FOOTBALL_DATA_KEY pour les vrais matchs)")
        build_matches()
        tasks = [asyncio.create_task(ticker())]
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="Foot Predict API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/config")
def get_config():
    return {"plans": PLANS, "whatsapp": WHATSAPP_NUMBER}


@app.get("/access/check")
def access_check(x_access_code: str = Header(default="")):
    expires = access.check_code(x_access_code)
    return {"valid": expires is not None, "expires": expires.isoformat() if expires else None}


@app.get("/admin/code")
def admin_code(key: str = "", plan: str = "", days: int = 30):
    """Crée un code après réception du paiement Wave. Exemples : /admin/code?key=...&plan=semaine ou &plan=mois"""
    if not ADMIN_KEY or not hmac.compare_digest(key.encode(), ADMIN_KEY.encode()):
        raise HTTPException(403, "Clé administrateur invalide")
    if plan:
        if plan not in PLAN_DAYS:
            raise HTTPException(400, "plan doit être « semaine » ou « mois »")
        days = PLAN_DAYS[plan]
    if not 1 <= days <= 366:
        raise HTTPException(400, "days doit être compris entre 1 et 366")
    code, expires = access.make_code(days)
    return {"code": code, "expires": expires.isoformat(), "days": days,
            "secret_fingerprint": access.fingerprint(), "secret_fixed": access.SECRET_IS_FIXED}


@app.get("/admin/rebuild")
async def admin_rebuild(key: str = ""):
    """Force la régénération immédiate des coupons et analyses du jour : /admin/rebuild?key=..."""
    if not ADMIN_KEY or not hmac.compare_digest(key.encode(), ADMIN_KEY.encode()):
        raise HTTPException(403, "Clé administrateur invalide")
    daily["stale"] = True
    await ensure_daily()
    today = today_str()
    return {"mode": mode(), "date": daily["date"], "matches_used": daily.get("matches_used", 0),
            "coupons": len(daily["coupons"]), "big_matches": len(daily["big_matches"]),
            # Diagnostic : où se perdent les matchs
            "matches_loaded": len(matches),
            "today_scheduled": sum(1 for m in matches.values() if m["status"] == "scheduled" and (m.get("kickoff") or today)[:10] == today),
            "today_scheduled_reliable": sum(1 for m in matches.values() if m["status"] == "scheduled" and m.get("reliable") and (m.get("kickoff") or today)[:10] == today),
            "teams_with_stats": len(real_strengths), "teams_with_form": len(real_form), "data_error": data_error()}


@app.get("/admin/diag")
async def admin_diag(key: str = ""):
    """Teste la clé football-data.org et montre la réponse brute : /admin/diag?key=..."""
    if not ADMIN_KEY or not hmac.compare_digest(key.encode(), ADMIN_KEY.encode()):
        raise HTTPException(403, "Clé administrateur invalide")
    if not USE_REAL:
        return {"mode": "demo", "detail": "FOOTBALL_DATA_KEY n'est pas défini"}
    import data_provider
    return await asyncio.to_thread(data_provider.diagnose)


@app.get("/daily")
def get_daily(x_access_code: str = Header(default="")):
    if access.check_code(x_access_code) is None:
        raise HTTPException(402, "Accès Premium requis")
    settle_all()
    out = {k: v for k, v in daily.items() if k not in ("ts", "stale")}
    if previous:
        out["previous"] = {k: previous[k] for k in ("date", "coupons") if k in previous}
    return out


@app.get("/matches")
def list_matches(x_access_code: str = Header(default="")):
    premium = access.check_code(x_access_code) is not None
    return [public_view(m, premium) for m in matches.values()]


@app.get("/matches/{match_id}")
def get_match(match_id: int, x_access_code: str = Header(default="")):
    if match_id not in matches:
        raise HTTPException(404, "Match introuvable")
    return public_view(matches[match_id], access.check_code(x_access_code) is not None)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    code = ws.query_params.get("code", "")
    clients[ws] = code
    try:
        await ws.send_json(payload_for(code))
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        clients.pop(ws, None)


# L'application web est servie par le même service
HERE = Path(__file__).parent


@app.get("/")
def home():
    return FileResponse(HERE / "index.html")


@app.get("/manifest.json")
def manifest():
    return FileResponse(HERE / "manifest.json", media_type="application/manifest+json")


@app.get("/icon.svg")
def icon():
    return FileResponse(HERE / "icon.svg", media_type="image/svg+xml")
