# Wacustom

> **Fork de [WAStream](https://gitlab.com/10ho/wastream) (10ho / spel, MIT).**
> Wacustom regroupe plusieurs sources francophones et internationales dans un seul addon, pensé pour être branché comme source unique dans un agrégateur (AIOStreams) plutôt que d'empiler plusieurs conteneurs.

**Ce que ce fork ajoute par rapport à WAStream :**

- **Sources consolidées en un seul addon** : Wawacity, Free-Telecharger, Movix, Webshare, trackers Torznab (YggReborn, Tr4ker, Torr9, C411), Zilean, et **Nyaa** (anime, flux RSS) - évite de maintenir 4-5 conteneurs séparés.
- **Support des torrents AllDebrid** : flux `magnet → upload → ready → files → unlock` (l'endpoint `/magnet/instant` ayant été retiré par AllDebrid, les torrents sont listés `uncached` avec débridage à la demande).
- **Matching d'épisodes anime robuste** : gestion de la numérotation absolue et des mappings de saisons (`episode_matches`).
- **Scraper Zilean** (index DMM public) et scraper UNIT3D natif (Gemini / Generation-Free).
- **Dédoublonnage interne** entre sous-sources (un même torrent remonté par plusieurs sources n'apparaît qu'une fois - complémentaire au dédoublonnage inter-addons d'AIOStreams).

---

<p align="center">
  <strong>Addon Stremio non officiel qui transforme des liens de téléchargement direct (DDL) en URLs lisibles en streaming via des services de débridage</strong>
</p>

---

## ⚠️ Avertissement important

**Wacustom est un utilitaire générique de résolution de liens.** C'est un outil technique qui prend un lien de téléchargement direct en entrée et renvoie une URL lisible en streaming en sortie, en utilisant un service de débridage comme intermédiaire.

**Cet outil n'héberge, ne distribue, ne fournit et ne promeut aucun contenu.** Il est agnostique quant au contenu par conception : il se contente de traiter le lien que l'utilisateur configure.

**Cas d'usage légitimes :**
- Lire ses propres sauvegardes personnelles stockées sur des hébergeurs de fichiers
- Accéder à du contenu du domaine public ou sous licence Creative Commons
- Stockage auto-hébergé et gestion de fichiers personnels
- Distribution de logiciels open source via DDL
- Tout contenu auquel l'utilisateur a légalement le droit d'accéder

**En utilisant cet outil, vous acceptez que :**
- Vous êtes seul responsable de vous assurer que vous avez le droit légal d'accéder au contenu traité
- Vous devez respecter toutes les lois applicables sur le droit d'auteur dans votre juridiction
- Vous devez respecter les conditions d'utilisation des sources et services que vous configurez
- Le développeur décline toute responsabilité en cas de mauvais usage de cet outil

---

## À propos

Wacustom est un addon Stremio qui convertit des liens de téléchargement direct en contenu lisible instantanément via des services de débridage. Il fournit un pipeline générique et configurable de scraping et de résolution, adaptable à n'importe quelle source DDL via des variables d'environnement.

### Fonctionnement

1. Vous configurez une ou plusieurs sources via des variables d'environnement
2. L'addon interroge ces sources quand Stremio demande les flux d'un titre donné
3. L'addon convertit les liens de téléchargement direct obtenus en URLs lisibles via le service de débridage de votre choix
4. Stremio lit le flux obtenu

### Ce que fait Wacustom

- **Scraping générique de sources** : interface de scraper configurable, compatible avec n'importe quelle source compatible
- **Intégration débridage** : résout les DDL en URLs lisibles via les services de débridage
- **Cache intelligent** : système de cache avec verrouillage distribué pour des performances optimales
- **Vérification de disponibilité instantanée** : vérifie la disponibilité du lien avant lecture
- **Filtrage avancé** : filtre par qualité, langue, taille, etc.
- **Support multi-langue** : gère plusieurs pistes audio et langues de sous-titres
- **Enrichissement des métadonnées** : intègre TMDB et Kitsu pour des métadonnées complètes

---

## Fonctionnalités

### Types de contenu supportés

- **Films** / **Séries** / **Anime**

### Services de débridage supportés

- **AllDebrid** - support multi-hébergeurs
- **TorBox** - support multi-hébergeurs
- **Premiumize** - support multi-hébergeurs
- **1Fichier** - 1Fichier premium direct
- **NZBDav** - Usenet via SABnzbd + WebDAV

### Fonctions intelligentes

- **Vérification de cache instantanée** - vérifie la disponibilité avant conversion
- **Traitement round-robin** - algorithme optimisé de vérification des liens
- **Filtrage par qualité** - filtre par résolution (4K, 1080p, 720p, etc.)
- **Filtrage par langue** - support multi-langue et sous-titres
- **Filtrage par taille** - filtre par taille de fichier
- **Dédoublonnage** - masque les résultats identiques provenant d'hébergeurs différents
- **Protection par mot de passe** - protection optionnelle de l'addon par mot de passe
- **Support base de données** - SQLite ou PostgreSQL
- **Scraper Pastebin** - import automatique de liens depuis des URLs de paste externes, planifié
- **Cache des réponses HTTP** - mise en cache optionnelle basée sur les ETag pour les réponses de flux
- **Intégration Kitsu** - métadonnées anime via l'API Kitsu avec mapping des chaînes de saisons

### Tableau de bord admin

- **Statistiques** - utilisateurs, recherches, flux, stats de cache
- **Logs en direct** - visualiseur de logs en temps réel avec filtrage
- **État des sources** - surveille la santé des sources et du proxy
- **Infos système** - utilisation RAM/CPU, état de la base de données
- **Gestion des liens morts** - ajouter/supprimer des liens morts
- **WASource** - ajouter des liens personnalisés compatibles avec tous les services de débridage
- **Système distant** - partager les liens morts, le cache et les liens personnalisés entre instances
- **Scraper Pastebin** - état et stats des tâches d'import automatique

---

## Installation

> **Pour la configuration de l'environnement, voir [`.env.example`](.env.example)**

### Docker Compose (recommandé)

Une image est publiée automatiquement (multi-arch `amd64`/`arm64`) sur GitHub Container Registry à chaque push sur `main` : `ghcr.io/dydy13014/wacustom:latest`. Pas besoin de cloner ni de builder pour l'utiliser.

1. **Récupérer la config d'exemple** :
```bash
curl -O https://raw.githubusercontent.com/dydy13014/wacustom/main/.env.example
cp .env.example wacustom.env
# Éditez wacustom.env selon vos besoins
```

2. **`docker-compose.yml`** :
```yaml
services:
  wacustom:
    image: ghcr.io/dydy13014/wacustom:latest
    container_name: wacustom
    env_file:
      - wacustom.env
    volumes:
      - wacustom-data:/app/data
    ports:
      - "7000:7000"
    restart: unless-stopped

volumes:
  wacustom-data:
```

3. **Démarrer** :
```bash
docker compose up -d
```

4. **Consulter les logs** :
```bash
docker compose logs -f wacustom
```

#### Build local (alternative)

Pour modifier le code ou builder vous-même l'image :
```bash
git clone https://github.com/dydy13014/wacustom.git
cd wacustom
cp .env.example wacustom.env
# Éditez wacustom.env selon vos besoins
docker compose up -d --build
```
(le `docker-compose.yml` du dépôt utilise `build: .` au lieu de l'image publiée)

### Installation manuelle

#### Prérequis
- Python 3.11 ou supérieur
- Git

#### Étapes

1. **Cloner le dépôt** :
```bash
git clone https://github.com/dydy13014/wacustom.git
cd wacustom
```

2. **Installer les dépendances** :
```bash
pip install .
```

3. **Configurer l'environnement** :
```bash
cp .env.example .env
# Éditez .env selon vos besoins
```

4. **Démarrer l'application** :
```bash
python -m wastream.main
```

---

## Configuration

### Ajouter à Stremio

1. **Accéder** à `http://localhost:7000` dans votre navigateur
2. **Configurer** vos clés API de débridage et votre token TMDB
3. **Cliquer** sur « Générer le lien »

L'addon apparaîtra dans votre liste d'addons Stremio.

### Tableau de bord admin

1. **Définir** `ADMIN_PASSWORD` dans votre fichier `.env`
2. **Accéder** à `http://localhost:7000/admin` dans votre navigateur
3. **Se connecter** avec votre mot de passe admin

### Variables d'environnement

Voir [`.env.example`](.env.example) pour toutes les options de configuration disponibles.

**Requis :**
- `SECRET_KEY` - clé de chiffrement des configs utilisateur (min. 32 caractères) - générer avec : `openssl rand -hex 32`
- Au moins une URL de source doit être configurée (voir `.env.example`)
- Clé API de débridage - configurée via l'interface web
- Token API TMDB - configuré via l'interface web

**Optionnels :**
- `ADMIN_PASSWORD` - mot de passe du tableau admin
- `ADDON_PASSWORD` - protéger votre addon par mot de passe
- `LOG_LEVEL` - niveau de log (DEBUG, INFO, ERROR)
- `PROXY_URL` - proxy HTTP pour l'accès aux sources
- `PASTEBIN_SCRAPER_URLS` - liste JSON d'URLs de paste pour l'import automatique
- Et bien d'autres… (voir `.env.example`)

---

## Dépannage

### Debug

```bash
# Vérification de santé
curl http://localhost:7000/health

# Logs Docker
docker compose logs -f wacustom

# Activer le mode debug
LOG_LEVEL=DEBUG python -m wastream.main
```

---

## Licence

Ce projet est sous licence MIT. Voir le fichier [LICENSE](LICENSE) pour plus de détails.

---

## Clause de non-responsabilité

**Wacustom est un projet non officiel, développé de manière indépendante.**

- **Non affilié à Stremio ni à aucun fournisseur de source/service**
- **Fourni « tel quel », sans aucune garantie d'aucune sorte**
- **Aucun contenu n'est hébergé, distribué ou promu par ce projet**
- **Le développeur décline toute responsabilité quant à l'usage que les utilisateurs font de cet outil**

C'est un outil technique aux usages légitimes. Il est de la seule responsabilité de l'utilisateur de s'assurer que son usage respecte les lois sur le droit d'auteur, les conditions d'utilisation et les réglementations applicables dans sa juridiction. Le développeur n'approuve, ne soutient ni ne cautionne aucun usage illégal de ce logiciel.

Si vous êtes un ayant droit et pensez que cet outil est utilisé de manière abusive, notez que ce dépôt ne contient qu'un utilitaire générique de résolution de liens et n'héberge, ne lie ni ne référence aucun contenu protégé spécifique. Toute demande de retrait doit être adressée aux sources réelles du contenu contrefaisant, et non à cet outil technique.

---
