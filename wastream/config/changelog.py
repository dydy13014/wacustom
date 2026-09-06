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
