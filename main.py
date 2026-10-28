"""API FastAPI : matchs de démonstration, prédictions avant-match et flux en direct (WebSocket).

IMPORTANT : les matchs « en direct » sont simulés pour la démonstration.
Pour la production, remplacer `simulate_tick` par les événements d'une vraie API football
(API-Football, football-data.org, Sportmonks...).
"""
import asyncio
import os
import random
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pathlib import Path

from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from model import predict_live, predict_prematch

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
clients: set[WebSocket] = set()


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
    from model import expected_goals
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
    return "reel" if os.getenv("FOOTBALL_API_KEY") else "demo"


def season():
    if not os.getenv("FOOTBALL_API_KEY"):
        return None
    import data_provider
    return data_provider.STRENGTH_SEASON


async def broadcast() -> None:
    payload = {"type": "update", "mode": mode(), "season": season(), "matches": list(matches.values())}
    for ws in list(clients):
        try:
            await ws.send_json(payload)
        except Exception:
            clients.discard(ws)


async def ticker() -> None:
    while True:
        await asyncio.sleep(2)  # 1 minute de match simulée toutes les 2 secondes
        for m in matches.values():
            simulate_tick(m)
        if all(m["status"] == "finished" for m in matches.values()):
            await asyncio.sleep(5)
            build_matches()
        await broadcast()


USE_REAL = bool(os.getenv("FOOTBALL_API_KEY"))
DEFAULT_TEAM = {"attack": 1.0, "defense": 1.0}
real_strengths: dict[str, dict] = {}


async def real_loop() -> None:
    """Mode réel : actualise les forces d'équipe (1 fois/jour) et les matchs (toutes les POLL_SECONDS)."""
    import data_provider
    last_strengths = 0.0
    while True:
        try:
            if time.time() - last_strengths > 86400:
                real_strengths.update(await asyncio.to_thread(data_provider.fetch_strengths))
                last_strengths = time.time()
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
                await broadcast()
            else:
                data_provider.log("[real_loop] quota API presque épuisé : pause jusqu'au renouvellement")
        except Exception as e:
            data_provider.log(f"[real_loop] erreur : {e}")
        await asyncio.sleep(data_provider.POLL_SECONDS)


@asynccontextmanager
async def lifespan(_: FastAPI):
    if USE_REAL:
        print("Mode RÉEL : données API-Football")
        task = asyncio.create_task(real_loop())
    else:
        print("Mode DÉMO : matchs simulés (définir FOOTBALL_API_KEY pour les vrais matchs)")
        build_matches()
        task = asyncio.create_task(ticker())
    yield
    task.cancel()


app = FastAPI(title="Foot Predict API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/matches")
def list_matches():
    return list(matches.values())


@app.get("/matches/{match_id}")
def get_match(match_id: int):
    if match_id not in matches:
        raise HTTPException(404, "Match introuvable")
    return matches[match_id]


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    clients.add(ws)
    try:
        await ws.send_json({"type": "update", "mode": mode(), "season": season(), "matches": list(matches.values())})
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        clients.discard(ws)


# L'application web est servie par le même service
HERE = Path(__file__).parent


@app.get("/diagnostic")
def diagnostic():
    """État du serveur pour comprendre un problème (ne contient jamais la clé API)."""
    import data_provider
    return {
        "mode": mode(),
        "cle_api_definie": bool(os.getenv("FOOTBALL_API_KEY")),
        "saison_des_statistiques": data_provider.STRENGTH_SEASON,
        "requetes_restantes_aujourd_hui": data_provider.remaining_requests,
        "intervalle_secondes": data_provider.POLL_SECONDS,
        "championnats_suivis": data_provider.LEAGUES,
        "nombre_de_matchs_affiches": len(matches),
        "derniers_messages": data_provider.LOG,
    }


@app.get("/")
def home():
    return FileResponse(HERE / "index.html")


@app.get("/manifest.json")
def manifest():
    return FileResponse(HERE / "manifest.json", media_type="application/manifest+json")


@app.get("/icon.svg")
def icon():
    return FileResponse(HERE / "icon.svg", media_type="image/svg+xml")
