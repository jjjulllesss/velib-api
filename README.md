# Velib Commute — API Serverless Vercel & Apple Shortcuts

Ce projet contient le code d'une fonction serverless Python (FastAPI) déployable sur Vercel, conçue pour être appelée depuis **Apple Shortcuts (Raccourcis)** sur iOS.

L'objectif est d'optimiser en temps réel votre trajet Vélib quotidien :
1. Sélectionner la meilleure station de départ avec au moins un **vélo mécanique disponible** (avec tri qualité par score, évaluation et fraîcheur).
2. Sélectionner la meilleure station d'arrivée ayant une **borne libre** pour stationner.
3. Générer un résumé court en français directly exploitable par Siri ou par notification.

---

## 🛠️ Structure du Projet

```text
├── api/
│   ├── helpers.py      # Fonctions asynchrones (requêtes parallèles, ranking, formatage)
│   └── index.py        # Point d'entrée FastAPI, validation Pydantic et route /api/commute
├── .gitignore          # Fichiers ignorés par Git (.venv, caches, etc.)
├── README.md           # Ce guide de documentation
├── requirements.txt    # Dépendances Python (FastAPI, Uvicorn, httpx, pydantic)
├── test_app.py         # Suite complète de tests unitaires (8 tests)
└── vercel.json         # Routage d'API Vercel
```

---

## 🚀 Installation & Développement Local

### 1. Prérequis
- Python 3.9 ou version ultérieure.

### 2. Initialisation du projet
Clonez le dépôt, créez un environnement virtuel et installez les dépendances :
```bash
# Créer l'environnement virtuel
python3 -m venv .venv

# Activer l'environnement
source .venv/bin/activate  # Sur macOS / Linux
# .venv\Scripts\activate   # Sur Windows

# Installer les paquets requis
pip install -r requirements.txt
```

### 3. Exécuter les tests
Une suite de tests unitaires valide l'intégralité de la logique métier sans dépendance réseau :
```bash
python3 -m unittest test_app.py
```

### 4. Lancer le serveur de développement local
Pour interroger l'API locale avec de vraies stations :
```bash
uvicorn api.index:app --reload
```
Le serveur démarrera sur `http://127.0.0.1:8000`. 
Vous pouvez accéder à la documentation interactive de l'API sur `http://127.0.0.1:8000/docs`.

---

## 📡 Utilisation de l'API

### Paramètres de Requête (`GET /api/commute`)
- `start` (Obligatoire) : Liste ordonnée d'identifiants de stations de départ (ex: `42024,1003,1021`).
- `end` (Obligatoire) : Liste ordonnée d'identifiants de stations d'arrivée (ex: `13053,13052`).

### Exemple de Requête
```text
GET http://127.0.0.1:8000/api/commute?start=42024,1003&end=13053,13052
```

### Exemple de Réponse JSON
```json
{
  "ok": true,
  "start_station_used": {
    "id": 42024,
    "name": "Danielle Casanova - Ledru-Rollin",
    "priority": 0
  },
  "selected_bikes": [
    {
      "id": "bike_81105",
      "dockPosition": "23",
      "score": 100,
      "bikeRate": 3,
      "lastRideTime": "2026-06-20T14:38:58.249Z"
    }
  ],
  "start_fallback_used": false,
  "no_mechanical_available": false,
  "end_station_used": {
    "id": 13053,
    "name": "Chevaleret - Tolbiac",
    "priority": 0,
    "docks_available": 1
  },
  "end_fallback_used": false,
  "no_docks_available": false,
  "summary": "Départ Danielle Casanova - Ledru-Rollin, 1 vélo mécanique trouvé. Arrivée Chevaleret - Tolbiac, 1 borne disponible.",
  "checked_at": "2026-06-20T15:30:00Z"
}
```

---

## ⚡ Déploiement sur Vercel

1. Installez la CLI Vercel si ce n'est pas déjà fait : `npm i -g vercel`.
2. Connectez-vous : `vercel login`.
3. Lancez le déploiement depuis le dossier du projet :
   ```bash
   vercel
   ```
4. Pour déployer en production :
   ```bash
   vercel --prod
   ```

---

## 📱 Intégration Apple Shortcuts

1. Créez un nouveau **Raccourci** sur votre iPhone.
2. Ajoutez l'action **Obtenir le contenu de l'URL** (`Get contents of URL`).
3. Configurez l'adresse avec l'URL de votre déploiement Vercel :
   `https://<votre-projet>.vercel.app/api/commute?start=42024,1003&end=13053,13052`
4. Ajoutez l'action **Obtenir la valeur du dictionnaire** (`Get Value for Key`) et demandez la clé `summary`.
5. Affichez le résultat dans une notification ou demandez à Siri de le prononcer !
