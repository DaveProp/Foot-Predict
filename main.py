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
wake = asyncio.Event()  # réveille real_loop à minuit


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

FREE_FIELDS = ("id", "league", "home", "away", "minute", "score", "status", "reliable", "events")


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


async def broadcast() -> None:
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
    global daily
    if daily["date"] == today_str() and (daily["coupons"] or time.time() - daily["ts"] < 1800):
        return
    statuses = {"scheduled"} if USE_REAL else {"scheduled", "live", "finished"}
    snapshot = [dict(m) for m in matches.values()]
    try:
        result = await asyncio.to_thread(coupons.build_daily, snapshot, dict(TEAMS), statuses)
    except Exception as e:
        print(f"[daily] erreur : {e}")
        return
    result["ts"] = time.time()
    daily = result
    print(f"[daily] {result['date']} : {len(result['coupons'])} coupons, {len(result['big_matches'])} grands matchs")


async def midnight_loop() -> None:
    """Réveille la mise à jour des données chaque jour à 00h00 (heure d'Abidjan = UTC)."""
    while True:
        now = datetime.now(timezone.utc)
        nxt = (now + timedelta(days=1)).replace(hour=0, minute=0, second=10, microsecond=0)
        await asyncio.sleep((nxt - now).total_seconds())
        wake.set()


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


async def real_loop() -> None:
    """Mode réel : actualise les forces d'équipe (1 fois/jour, à minuit) et les matchs (toutes les POLL_SECONDS)."""
    import data_provider
    last_strengths = 0.0
    while True:
        try:
            if time.time() - last_strengths > 86400:
                got = await asyncio.to_thread(data_provider.fetch_strengths)
                real_strengths.update(got)
                # Échec (clé refusée, réseau...) : nouvel essai dans 30 min au lieu de 24 h
                last_strengths = time.time() if got else time.time() - 86400 + 1800
            if not data_provider.quota_low():
                fresh = await asyncio.to_thread(data_provider.fetch_matches)
                matches.clear()
                for m in fresh:
                    for team in (m["home"], m["away"]):
                        TEAMS[team] = real_strengths.get(team, TEAMS.get(team, DEFAULT_TEAM))
                    # Prédiction fiable seulement si les deux équipes ont de vraies statistiques
                    m["reliable"] = m["home"] in real_strengths and m["away"] in real_strengths
                    refresh(m)
                    matches[m["id"]] = m
                await ensure_daily()
                await broadcast()
            else:
                print("[real_loop] quota API presque épuisé : pause jusqu'au renouvellement")
        except Exception as e:
            print(f"[real_loop] erreur : {e}")
        try:
            await asyncio.wait_for(wake.wait(), data_provider.POLL_SECONDS)
            last_strengths = 0.0  # minuit : on recalcule aussi les forces d'équipe
        except asyncio.TimeoutError:
            pass
        wake.clear()


@asynccontextmanager
async def lifespan(_: FastAPI):
    if not access.SECRET_IS_FIXED:
        print("ATTENTION : ACCESS_SECRET non défini, les codes d'accès seront invalidés à chaque redémarrage")
    if not ADMIN_KEY:
        print("ATTENTION : ADMIN_KEY non défini, impossible de créer des codes d'accès")
    if USE_REAL:
        print("Mode RÉEL : données football-data.org")
        tasks = [asyncio.create_task(real_loop()), asyncio.create_task(midnight_loop())]
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
    daily["date"] = None
    await ensure_daily()
    return {"mode": mode(), "date": daily["date"], "matches_used": daily.get("matches_used", 0),
            "coupons": len(daily["coupons"]), "big_matches": len(daily["big_matches"])}


@app.get("/daily")
def get_daily(x_access_code: str = Header(default="")):
    if access.check_code(x_access_code) is None:
        raise HTTPException(402, "Accès Premium requis")
    return {k: v for k, v in daily.items() if k != "ts"}


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
