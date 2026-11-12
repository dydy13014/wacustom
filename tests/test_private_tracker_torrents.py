"""Les .torrent des trackers prives ne vivent
qu'en memoire ; apres un redemarrage, des resultats de ces trackers restent en cache
en base (CONTENT_CACHE_TTL) SANS leur .torrent -> magnet nu, "No peer".
- purge de ces entrees au demarrage (vraie base SQLite) ;
- UNIT3D enregistre enfin le .torrent, au nom du proprietaire de la cle.
"""
import pytest
from databases import Database

from wastream.scrapers.unit3d import base as u3d
from wastream.services import content_cache, torrent_files

HASH = "b" * 40


@pytest.fixture
async def db(tmp_path, monkeypatch):
    database = Database(f"sqlite+aiosqlite:///{tmp_path}/t.db")
    await database.connect()
    await database.execute("CREATE TABLE content_cache (cache_key TEXT PRIMARY KEY, content TEXT NOT NULL, expires_at INTEGER)")
    monkeypatch.setattr(content_cache, "database", database)
    rebuilds = []

    async def fake_rebuild():
        rebuilds.append(1)

    monkeypatch.setattr(content_cache, "rebuild_cache_stats", fake_rebuild)
    database.rebuilds = rebuilds
    yield database
    await database.disconnect()


async def _keys(database):
    rows = await database.fetch_all("SELECT cache_key FROM content_cache ORDER BY cache_key")
    return [r["cache_key"] for r in rows]


async def _fill(database, keys):
    for k in keys:
        await database.execute("INSERT INTO content_cache VALUES (:k, '[]', -1)", {"k": k})


async def test_purge_supprime_les_trackers_prives_et_garde_le_reste(db):
    prives = [
        "c411_movie:le+parrain:1972", "c411_series_s1e5:show", "c411-ab12cd_movie:x",
        "yggreborn_movie:a", "tr4ker_anime:b", "torr9_series:c", "v3x_movie:d",
        "gemini_movie:e", "generation_free_movie:f", "generation_free-9f8e_series:g",
    ]
    autres = ["wawacity_movie:x", "nyaa_series:y", "zilean_movie:z", "lumio_movie:l", "aiosources_movie:a",
              "c4111_movie:non", "c4110-x_movie:non", "gemini2_movie:non"]
    await _fill(db, prives + autres)
    supprimees = await content_cache.purge_private_tracker_cache()
    assert supprimees == len(prives)
    assert await _keys(db) == sorted(autres)
    assert db.rebuilds == [1]  # statistiques recalculees une fois


async def test_underscore_de_generation_free_n_est_pas_un_joker(db):
    await _fill(db, ["generationXfree_movie:a", "generation_free_movie:b"])
    await content_cache.purge_private_tracker_cache()
    assert await _keys(db) == ["generationXfree_movie:a"]


async def test_purge_sans_rien_a_supprimer(db):
    await _fill(db, ["wawacity_movie:x"])
    assert await content_cache.purge_private_tracker_cache() == 0
    assert db.rebuilds == []


async def test_purge_ne_leve_jamais(monkeypatch):
    class Casse:
        async def fetch_val(self, *a, **k):
            raise RuntimeError("base indisponible")
    monkeypatch.setattr(content_cache, "database", Casse())
    assert await content_cache.purge_private_tracker_cache() == 0  # le demarrage continue


# ------------------------------------------------------------------ UNIT3D
class _Resp:
    status_code = 200

    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


class _FakeHttp:
    def __init__(self, data):
        self.data = data

    async def get(self, url, **kw):
        return _Resp(self.data)


def _payload(download_link="https://tracker.example/torrent/download/12.RSSKEY"):
    return {"data": [{"attributes": {
        "name": "Le.Parrain.1972.MULTi.1080p.BluRay.x264-GRP", "info_hash": HASH.upper(),
        "size": 8 * 1024 ** 3, "download_link": download_link}}]}


@pytest.fixture(autouse=True)
def _registre_propre():
    torrent_files._registry.clear()
    yield
    torrent_files._registry.clear()


async def test_unit3d_enregistre_le_torrent_au_nom_du_proprietaire(monkeypatch):
    monkeypatch.setattr(u3d, "http_client", _FakeHttp(_payload()))
    scraper = u3d.BaseUnit3d("Generation-Free", "https://tracker.example", "tok", key_owner="user42")
    res = await scraper.search("Le Parrain", "1972")
    assert len(res) == 1
    assert (HASH, "user42") in torrent_files._registry
    source, url, _ = torrent_files._registry[(HASH, "user42")]
    assert source == "Generation-Free" and url.endswith("RSSKEY")
    # l'URL (qui porte la cle) ne sort jamais dans le resultat mis en cache
    assert "RSSKEY" not in repr(res) and res[0]["link"] == f"magnet:?xt=urn:btih:{HASH}"


async def test_unit3d_proprietaire_global_par_defaut(monkeypatch):
    from wastream.utils.tracker_keys import GLOBAL_OWNER
    monkeypatch.setattr(u3d, "http_client", _FakeHttp(_payload()))
    await u3d.BaseUnit3d("Gemini", "https://tracker.example", "tok").search("Le Parrain", "1972")
    assert (HASH, GLOBAL_OWNER) in torrent_files._registry


async def test_unit3d_sans_lien_de_telechargement_rien_n_est_enregistre(monkeypatch):
    monkeypatch.setattr(u3d, "http_client", _FakeHttp(_payload(download_link=None)))
    res = await u3d.BaseUnit3d("Gemini", "https://tracker.example", "tok").search("Le Parrain", "1972")
    assert len(res) == 1 and not torrent_files._registry


def test_le_demarrage_appelle_la_purge():
    import inspect
    from wastream import main
    src = inspect.getsource(main.lifespan)
    assert "await purge_private_tracker_cache()" in src
    assert src.index("apply_startup_overrides()") < src.index("purge_private_tracker_cache()")
