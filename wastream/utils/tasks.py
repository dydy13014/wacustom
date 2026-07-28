"""Lancement de tâches de fond « à oublier », sans risque de disparition.

`asyncio.create_task()` seul ne suffit pas : l'event loop ne conserve qu'une
référence FAIBLE aux tâches. Une tâche dont plus personne ne détient le handle
peut donc être ramassée par le garbage collector **en pleine exécution**, ce qui
l'annule sans le moindre message. Le comportement est documenté par CPython et
non déterministe — il ne laisse aucune trace, ce qui le rend particulièrement
pénible à diagnostiquer.

Le cas le plus coûteux ici était le rafraîchissement de cache en arrière-plan
(`services/stream.py`) : il relance une recherche complète, de plusieurs
secondes à une trentaine, avant d'écrire le cache. Interrompu, le cache n'est
jamais écrit et l'utilisateur retombe indéfiniment sur des recherches lentes.

Usage :
    from wastream.utils.tasks import lancer_tache
    lancer_tache(ma_coroutine())
"""
import asyncio
from typing import Any, Coroutine, Optional, Set

from wastream.utils.logger import api_logger

# Référence FORTE tant que la tâche vit — c'est tout l'intérêt du module.
_taches: Set[asyncio.Task] = set()


def _terminee(tache: asyncio.Task) -> None:
    _taches.discard(tache)
    if tache.cancelled():
        return
    exc = tache.exception()
    if exc is not None:
        # Sans ce log, l'exception d'une tâche détachée est silencieuse jusqu'à
        # ce que le ramasse-miettes la signale, souvent bien plus tard et sans
        # contexte exploitable.
        api_logger.error(f"[Tâche de fond] {tache.get_name()} : {type(exc).__name__}: {exc}")


def lancer_tache(coro: Coroutine[Any, Any, Any], nom: Optional[str] = None) -> asyncio.Task:
    """Lance `coro` en tâche de fond en gardant une référence jusqu'à sa fin."""
    tache = asyncio.create_task(coro, name=nom or getattr(coro, "__name__", "anonyme"))
    _taches.add(tache)
    tache.add_done_callback(_terminee)
    return tache


def taches_en_cours() -> int:
    return len(_taches)
