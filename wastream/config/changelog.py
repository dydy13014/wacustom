"""Historique des versions de Wacustom.

Numérotation propre au fork, indépendante de celle de WAStream (cf.
`WACUSTOM_VERSION` / `WASTREAM_BASE_VERSION` dans settings.py). Nouvelle entrée
en tête de liste à chaque version : c'est ce que la page /configure affiche
quand on clique sur le numéro de version.

Rédigé pour être lu par un utilisateur, pas par un développeur : dire ce que ça
change à l'usage, pas le détail d'implémentation.
"""

CHANGELOG = [
    {
        "version": "1.4.0",
        "date": "2026-09-25",
        "changes": [
            "AIOSources et Lumio ne remontaient plus aucun résultat depuis un moment, à cause d'un bug silencieux dans la transmission de l'identifiant IMDB aux sources — corrigé, ces deux sources fonctionnent de nouveau normalement",
            "Recherche plus précise sur C411, Tr4ker et V3X : la correspondance se fait désormais par identifiant exact (IMDB/TMDB) plutôt que par simple recherche de mots-clés, ce qui réduit les faux résultats sur les titres ambigus",
            "Détection plus fiable des pannes de tracker (clé expirée, quota dépassé) plutôt qu'un silence indiscernable d'un simple \"aucun résultat\"",
            "Stremio se souvient maintenant de la source/qualité choisie et la reprend automatiquement à l'épisode suivant, sans avoir à rechoisir à chaque fois",
            "Taille du fichier transmise à Stremio quand elle est connue (torrents), utile pour l'affichage et le tri côté application",
            "Meilleure couverture des séries sur Tr4ker : utilise maintenant le bon identifiant (TheTVDB) qu'il attend en priorité, plutôt qu'un identifiant de repli — plus de résultats trouvés sur certains titres",
            "Zilean : la taille des fichiers s'affichait comme \"Inconnu\" sur tous les résultats — corrigé. Recherche également plus précise, par identifiant IMDB exact",
        ],
    },
    {
        "version": "1.3.1",
        "date": "2026-09-23",
        "changes": [
            "Torrents des trackers privés (C411, Tr4ker, YggReborn, V3X) pas encore en cache : le téléchargement démarre désormais vraiment chez le service debrid, grâce à l'envoi du vrai fichier .torrent au lieu d'un simple lien magnet qui ne trouvait jamais de source",
            "Recherche avec les titres alternatifs (titre français différent du titre original) : relancée aussi quand seuls les trackers torrent n'ont rien trouvé, et limitée à ces trackers pour ne pas ralentir la réponse",
        ],
    },
    {
        "version": "1.3.0",
        "date": "2026-09-16",
        "changes": [
            "Nouvelle source disponible : AIOSources (agrégateur communautaire C411/Tr4ker/TsukiHime/Nostradamus/TheOldSchool, projet tiers maintenu par Théo [TB]) — réglée une seule fois par l'hébergeur, comme Zilean ou Nyaa : aucune clé à renseigner pour en profiter",
        ],
    },
    {
        "version": "1.2.0",
        "date": "2026-09-09",
        "changes": [
            "Nouvelle identité visuelle : dégradé cyan/indigo et nouveau logo, sur la page de configuration, la connexion admin et le tableau de bord (merci à razeN pour le logo !)",
            "Wacustom repart de la base WAStream 3.9.1 (le fork suivait jusqu'ici la 3.8.2)",
            "Nouvelle page de statut public (désactivée par défaut) : disponibilité des sources et des hébergeurs en un coup d'œil, avec un court historique des dernières pannes",
            "Nouvelle option Réglages : vérification des liens morts en 2 temps (« Recheck dead links »). Un lien qui échoue une première fois reste visible pour une seconde tentative avant d'être considéré définitivement mort",
            "Fiabilité Idrix : délai entre les requêtes allongé pour réduire les erreurs, meilleure détection des vraies pages de contenu",
            "Meilleure compatibilité des sources : repli automatique www/non-www lors de la synchronisation des domaines, en-têtes de requête plus cohérents",
            "AllDebrid : sélection plus fiable du lien à débrider quand plusieurs choix sont proposés, et distinction entre un lien réellement mort et une simple erreur temporaire du service",
        ],
    },
    {
        "version": "1.1.1",
        "date": "2026-09-06",
        "changes": [
            "Petites retouches de texte dans l'interface (tirets longs remplacés par une ponctuation plus lisible)",
        ],
    },
    {
        "version": "1.1.0",
        "date": "2026-08-21",
        "changes": [
            "Nouvelle option Réglages : prioriser les résultats en VF/Multi (doublage français) avant les résultats VOSTFR, parmi les résultats français",
        ],
    },
    {
        "version": "1.0.0",
        "date": "2026-08-16",
        "changes": [
            "Wacustom repart de la base WAStream 3.8.2 (le fork suivait jusqu'ici la 3.6.3)",
            "Wacustom a désormais sa propre identité : logo, numéro de version distinct de celui de WAStream, et liens vers Wacustom, WAStream et le Discord",
            "Interface revue : navigation par onglets, palette unifiée entre la page de configuration, la connexion et le tableau de bord",
            "Cliquer sur le numéro de version affiche les nouveautés (ce changelog)",
            "Nouvel onglet Réglages : les sources et leurs clés API se configurent depuis le tableau de bord, sans éditer de fichier",
            "Clés API des trackers (Tr4ker, C411, Torr9, YggReborn, V3X, Gemini, Generation-Free) configurables depuis l'interface, stockées chiffrées",
            "Onglet Sauvegarde : export et import complets des données",
            "Nouveau tracker disponible : V3X, comme les autres il ne s'active qu'en renseignant sa propre clé",
            "Nouvelle source Lumio : chacun peut renseigner son propre manifest Lumio (torrents déjà vérifiés en cache debrid), au même titre qu'une clé de tracker",
            "Nouvelles options disponibles, désactivées par défaut : source Zone-Telechargement, collecteur Idrix, et suivi automatique des changements de domaine des sites sources",
            "Animés : un fichier nommé « S3 - 07 » n'est plus pris pour la saison 3 entière, il ne correspond plus qu'à l'épisode 7 (les mauvais épisodes n'apparaissent plus dans la liste)",
            "Animés : les lots d'épisodes (« 1017-1024 ») sont désormais reconnus sur toute leur plage, et une année ou un codec dans le nom ne passe plus pour un numéro d'épisode",
            "Fiabilité : un hébergeur AllDebrid renvoyant des informations incomplètes ne fait plus disparaître le statut des autres (moins de sources marquées indisponibles à tort)",
        ],
    },
]
