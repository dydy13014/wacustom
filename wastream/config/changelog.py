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
        "version": "1.0.0",
        "date": "2026-08-15",
        "changes": [
            "Wacustom repart de la base WAStream 3.8.2 (le fork suivait jusqu'ici la 3.6.3)",
            "Nouveau tracker disponible : V3X — comme les autres, il ne s'active qu'en renseignant sa propre clé",
            "Nouvel onglet Réglages : les sources et leurs clés API se configurent depuis le tableau de bord, sans éditer de fichier",
            "Clés API des trackers (Tr4ker, C411, Torr9, YggReborn, V3X, Gemini, Generation-Free) configurables depuis l'interface, stockées chiffrées",
            "Onglet Sauvegarde : export et import complets des données",
            "Nouvelles options disponibles, désactivées par défaut : source Zone-Telechargement, collecteur Idrix, et suivi automatique des changements de domaine des sites sources",
            "Interface revue : navigation par onglets, palette unifiée entre la page de configuration, la connexion et le tableau de bord",
            "Wacustom a désormais son propre numéro de version, distinct de celui de WAStream",
            "Animés : un fichier nommé « S3 - 07 » n'est plus pris pour la saison 3 entière, il ne correspond plus qu'à l'épisode 7 (les mauvais épisodes n'apparaissent plus dans la liste)",
            "Animés : les lots d'épisodes (« 1017-1024 ») sont désormais reconnus sur toute leur plage, et une année ou un codec dans le nom ne passe plus pour un numéro d'épisode",
        ],
    },
]
