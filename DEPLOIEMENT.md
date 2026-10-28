# Mettre Foot Predict en ligne (gratuit, ~15 minutes)

Résultat : une adresse web (ex. `https://foot-predict.onrender.com`) que tu ouvres sur n'importe quel téléphone,
avec les vrais matchs. Tu peux aussi l'installer sur l'écran d'accueil comme une application.

Tu as besoin de : un compte **GitHub** (gratuit) et un compte **Render** (gratuit, connexion possible avec GitHub).

## Étape 1 : mettre le projet sur GitHub

1. Va sur github.com, connecte-toi, clique sur **New repository** (bouton « + » en haut à droite).
2. Nom : `foot-predict`. Choisis **Private** (privé). Clique **Create repository**.
3. Sur la page du dépôt, clique **uploading an existing file**.
4. Décompresse `foot-predict.zip` sur ton téléphone ou ordinateur, puis glisse **tout le contenu du dossier** `foot-predict`
   (les dossiers `backend` et `app`, et les fichiers `render.yaml`, `README.md`...) dans la page.
   Sur ordinateur, glisser-déposer fonctionne. Sur téléphone, il est plus simple d'utiliser un ordinateur pour cette étape.
5. Clique **Commit changes**.

> Ne mets JAMAIS ta clé API dans ces fichiers. Elle sera saisie à l'étape 3, directement sur Render.

## Étape 2 : créer le service sur Render

1. Va sur render.com, clique **Get Started**, connecte-toi avec GitHub.
2. Clique **New +** puis **Blueprint** (ou **Web Service** si Blueprint n'apparaît pas).
3. Choisis ton dépôt `foot-predict` (autorise Render à y accéder).
4. Avec **Blueprint** : Render lit `render.yaml` et prépare tout. Il te demande la valeur de `FOOTBALL_API_KEY` : colle ta clé API-Football.
   Avec **Web Service** (réglages manuels) :
   - Root Directory : `backend`
   - Build Command : `pip install -r requirements.txt`
   - Start Command : `uvicorn main:app --host 0.0.0.0 --port $PORT`
   - Instance type : **Free**
   - Environment : ajoute `FOOTBALL_API_KEY` = ta clé, et `POLL_SECONDS` = `900`
5. Clique **Deploy**. Attends 2 à 5 minutes que l'état passe à **Live**.

## Étape 3 : ouvrir l'application

Render affiche l'adresse du service en haut de la page (se termine par `.onrender.com`). Ouvre-la :
- En haut à droite, tu dois voir **« Données réelles »**. Si tu vois **« Simulation (démo) »**, la clé n'a pas été prise en compte :
  vérifie le nom `FOOTBALL_API_KEY` dans Render > Environment.
- Sur Android (Chrome) : menu ⋮ puis **Ajouter à l'écran d'accueil**. Sur iPhone (Safari) : bouton Partager puis **Sur l'écran d'accueil**.

## À savoir

- **Veille gratuite** : sur l'offre gratuite de Render, le service s'endort après 15 minutes sans visite. Le premier chargement
  suivant prend environ 30 à 60 secondes. L'offre payante (environ 7 $/mois) supprime ce délai.
- **Quota API-Football** : l'offre gratuite est limitée (environ 100 requêtes par jour). Avec `POLL_SECONDS=900`,
  l'application se met à jour toutes les 15 minutes. Avec une offre payante, mets `POLL_SECONDS=30` pour du quasi temps réel.
- **Saison** : si les classements ne se chargent pas (message dans les « Logs » de Render), ajoute la variable `SEASON`
  avec une saison que ton offre autorise (ex. `2023`).
- **Prédictions** : ce sont des estimations statistiques, jamais des certitudes, et elles ne doivent pas servir de garantie pour des paris.
- **Si ça ne marche pas** : sur Render, ouvre l'onglet **Logs**, copie les dernières lignes d'erreur et envoie-les-moi.
