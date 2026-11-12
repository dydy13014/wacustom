"""Anti-rafale Torznab :
- pertinence testee sur tous les titres connus (pas seulement le principal) ;
- une recherche par identifiant n'est pas refaite pour chaque titre alternatif ;
- apres un 429 le tracker est mis en pause, sans memoriser l'echec ;
- la sonde de sante des trackers a cle est espacee, et un 429 = en ligne.
Aucun appel reseau : le client HTTP est factice.
"""
import time

import pytest

from wastream.scrapers.torznab import base as tz
from wastream.services import health

HASH = "a" * 40
XML = f"""<?xml version="1.0"?>
<rss xmlns:torznab="http://torznab.com/schemas/2015/feed"><channel>
<item><title>{{name}}</title>
<enclosure url="magnet:?xt=urn:btih:{HASH}" length="1000"/>
<torznab:attr name="infohash" value="{HASH}"/>
{{extra}}
</item></channel></rss>""".encode()


class _Resp:
    def __init__(self, status=200, content=b"", headers=None):
        self.status_code, self.content, self.headers = status, content, headers or {}


class _FakeHttp:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    async def get(self, url, **kw):
        self.calls.append((url, kw))
        r = self.responses[min(len(self.calls), len(self.responses)) - 1]
        return r


def _xml(name="Le.Parrain.1972.MULTi.1080p.BluRay.x264-GRP", extra=""):
    return XML.replace(b"{name}", name.encode()).replace(b"{extra}", extra.encode())


@pytest.fixture(autouse=True)
def _etat_propre(monkeypatch):
    tz._GATES.clear()
    tz._ID_MEMO.clear()
    monkeypatch.setattr(tz, "_MIN_INTERVAL", 0.0)
    monkeypatch.setattr(tz.settings, "TORZNAB_RETRY_DELAY", 0.0, raising=False)


def _tracker():
    return tz.BaseTorznab("Test", "https://tracker.example/api", "cle", movie_id_params=["tmdbid"],
                          tv_id_params=["tmdbid"])


META = {"tmdb_id": "238", "titles": ["The Godfather", "Le Parrain"]}


async def test_titre_traduit_accepte_en_recherche_par_id(monkeypatch):
    http = _FakeHttp(_Resp(200, _xml()))
    monkeypatch.setattr(tz, "http_client", http)
    res = await _tracker().search("The Godfather", "1972", META)
    assert len(res) == 1  # avant : rejete, le titre principal est l'anglais


async def test_hors_sujet_toujours_rejete(monkeypatch):
    http = _FakeHttp(_Resp(200, _xml("Totalement.Autre.Chose.2020.1080p")))
    monkeypatch.setattr(tz, "http_client", http)
    assert await _tracker().search("The Godfather", "1972", META) == []


async def test_recherche_par_id_non_repetee_pour_un_titre_alternatif(monkeypatch):
    http = _FakeHttp(_Resp(200, _xml()))
    monkeypatch.setattr(tz, "http_client", http)
    t = _tracker()
    r1 = await t.search("The Godfather", "1972", META)
    r2 = await t.search("Le Parrain", "1972", META)
    assert len(http.calls) == 1
    assert r1 == r2 and len(r2) == 1
    r2[0]["link"] = "modifie"  # le memo rend des copies
    assert (await t.search("The Godfather", "1972", META))[0]["link"] != "modifie"


async def test_echec_non_memorise(monkeypatch):
    http = _FakeHttp(_Resp(404, b""), _Resp(200, _xml()))
    monkeypatch.setattr(tz, "http_client", http)
    t = _tracker()
    assert await t.search("The Godfather", "1972", META) == []
    assert len(await t.search("The Godfather", "1972", META)) == 1
    assert len(http.calls) == 2


async def test_429_met_le_tracker_en_pause(monkeypatch):
    http = _FakeHttp(_Resp(429, b"", {"Retry-After": "5"}), _Resp(200, _xml()))
    monkeypatch.setattr(tz, "http_client", http)
    t = _tracker()
    assert await t.search("The Godfather", "1972", META) == []
    gate = tz._gate_for(t.url)
    assert 0 < gate.paused_for() <= 5
    # pendant la pause : aucune requete supplementaire (autre identifiant, donc pas de memo)
    assert await t.search("Autre", "2000", {"tmdb_id": "999", "titles": ["Autre"]}) == []
    assert len(http.calls) == 1
    gate.blocked_until = 0  # fin de pause
    assert len(await t.search("The Godfather", "1972", META)) == 1


def test_retry_after_plafonne_et_valeur_par_defaut():
    assert tz._retry_after_seconds(_Resp(429, b"", {"Retry-After": "9999"})) == tz._PAUSE_429_MAX
    assert tz._retry_after_seconds(_Resp(429, b"", {})) == tz._PAUSE_429_DEFAULT
    assert tz._retry_after_seconds(_Resp(429, b"", {"Retry-After": "abc"})) == tz._PAUSE_429_DEFAULT


async def test_attribut_size_sans_valeur_ne_perd_pas_la_reponse(monkeypatch):
    extra = '<torznab:attr name="size"/>'
    http = _FakeHttp(_Resp(200, _xml(extra=extra)))
    monkeypatch.setattr(tz, "http_client", http)
    assert len(await _tracker().search("The Godfather", "1972", META)) == 1


# ------------------------------------------------------------------ sonde de sante
@pytest.fixture
def _sante(monkeypatch):
    health._health_state.sources.clear()
    health._tracker_probe_sig.clear()
    monkeypatch.setattr(health.settings, "C411_API_KEY", "cle")
    return monkeypatch


async def test_sonde_tracker_espacee_hors_force(_sante):
    http = _FakeHttp(_Resp(200, b"<caps/>"))
    _sante.setattr(health, "http_client", http)
    url = "https://tracker.example/api"
    s1 = await health._check_single_source("C411", url, force=False)
    health._health_state.sources["C411"] = s1
    assert s1.status == "online" and len(http.calls) == 1
    assert "apikey" in http.calls[0][1]["params"] and "apikey" not in http.calls[0][0]  # cle en params
    await health._check_single_source("C411", url, force=False)
    assert len(http.calls) == 1  # espace : pas de nouvelle requete
    await health._check_single_source("C411", url, force=True)
    assert len(http.calls) == 2  # verification manuelle : toujours


async def test_sonde_repart_si_la_cle_change(_sante):
    http = _FakeHttp(_Resp(200, b"<caps/>"))
    _sante.setattr(health, "http_client", http)
    url = "https://tracker.example/api"
    health._health_state.sources["C411"] = await health._check_single_source("C411", url, force=False)
    _sante.setattr(health.settings, "C411_API_KEY", "autre-cle")
    await health._check_single_source("C411", url, force=False)
    assert len(http.calls) == 2


async def test_sonde_429_compte_comme_en_ligne(_sante):
    http = _FakeHttp(_Resp(429, b""))
    _sante.setattr(health, "http_client", http)
    s = await health._check_single_source("C411", "https://tracker.example/api", force=True)
    assert s.status == "online" and "429" in (s.error or "")


async def test_sonde_expire_apres_l_intervalle(_sante):
    http = _FakeHttp(_Resp(200, b"<caps/>"))
    _sante.setattr(health, "http_client", http)
    url = "https://tracker.example/api"
    s = await health._check_single_source("C411", url, force=False)
    s.last_check = time.time() - health._TRACKER_PROBE_MIN_INTERVAL - 1
    health._health_state.sources["C411"] = s
    await health._check_single_source("C411", url, force=False)
    assert len(http.calls) == 2
