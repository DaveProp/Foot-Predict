# Foot Predict ⚽

**Pour mettre l'application en ligne, suivre `DEPLOIEMENT.md`.** Le backend sert aussi l'application web
(dossier `backend/static`), donc un seul service suffit ; le dossier `app/` (Expo) est une option pour une application native plus tard.

Application de prédiction de football en temps réel (web + Android + iOS, une seule base de code).

- `backend/` : API Python (FastAPI) + modèle statistique (Poisson / Dixon-Coles) + flux temps réel (WebSocket)
- `app/` : application Expo (React Native) : web, Android, iOS

> Les probabilités sont des estimations statistiques, jamais des certitudes.
> Les matchs sont **simulés** (données de démonstration) tant qu'aucune API football n'est branchée.

## Lancer le backend

```bash
cd backend
pip install -r requirements.txt
python -m pytest            # tests du modèle
uvicorn main:app --host 0.0.0.0 --port 8000
```

## Lancer l'application

```bash
cd app
npm install
npm run web                 # version web
npm start                   # puis scanner le QR code avec l'app Expo Go (Android/iOS)
```

Sur téléphone, modifier `app/config.js` : remplacer `localhost` par l'adresse IP de l'ordinateur (ex. `http://192.168.1.20:8000`).

## Vraies données (API-Football)

Sans clé, le backend tourne en **mode démo** (matchs simulés). Avec une clé API-Football, il passe en **mode réel** :
matchs du jour de 7 ligues, et forces attaque/défense calculées chaque jour depuis les classements.

```bash
# Linux / Mac
export FOOTBALL_API_KEY="ta_cle"
# Windows (PowerShell)
$env:FOOTBALL_API_KEY="ta_cle"

uvicorn main:app --host 0.0.0.0 --port 8000
```

Ne jamais écrire la clé dans le code ni la partager publiquement.

Réglages optionnels (variables d'environnement) :
- `POLL_SECONDS` : intervalle d'actualisation (900 par défaut, adapté à l'offre gratuite, ~100 requêtes/jour). Avec une offre payante : 30 ou 60.
- `SEASON` : saison utilisée pour les classements (ex. `2025`). Si l'offre gratuite refuse la saison récente, essayer une saison plus ancienne.

Limites actuelles : les cartons rouges ne sont pas récupérés (économie de requêtes) et le fichier `backend/data_provider.py`
n'a pas pu être testé contre la vraie API depuis mon environnement ; si une erreur s'affiche dans le terminal, la copier pour la corriger.

## Publier

- Web : `npx expo export --platform web` puis hébergement statique ; le backend sur un serveur (Render, Railway, VPS).
- Mobile : `eas build` (Expo Application Services) pour générer les fichiers Android (.aab) et iOS.
