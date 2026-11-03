"""Verifications autonomes (sans pytest) de la source Viewio.
Lancer : python tests/check_viewio.py (variables d'environnement de test requises)."""
import asyncio
from wastream.config.settings import settings
settings.VIEWIO_MANIFEST_URL = "https://addon.viewio.me/u/test-jeton/manifest.json"
from wastream.scrapers.viewio import base as vb
import wastream.services.stream as st

fails = []
def check(cond, msg):
    print(("OK    " if cond else "ECHEC ") + msg)
    if not cond: fails.append(msg)

def play(i=0): return f"https://addon.viewio.me/playback/AAAA{i}.BBBB"

# --- 1. parseur sur la structure observee le 2026-11-02 --------------------
torrent = {"name": "[AllDebrid ⚡] Viewio 720p", "title": "780 Mo · 0,9 Mb/s\nBluRay · HI", "url": play(1),
           "behaviorHints": {"bingeGroup": "viewio|opaque|viewio%7Ctorrent%7C720p%7Cfr%7Cknaben", "videoSize": 818287738,
                             "indexer": "Knaben", "cached": True,
                             "folderName": "Spider-Man (2002) MULTi VFF 720p BluRay x264"}}
ddl = {"name": "[AllDebrid ⚡] Viewio SD", "title": "1,4 Go · 1,6 Mb/s\nBDRip · FR", "url": play(2),
       "behaviorHints": {"bingeGroup": "viewio|opaque|viewio%7Cddl%7Csd%7Cfr%7Cwawacity", "videoSize": 1471026298, "cached": True,
                         "indexer": "Wawacity", "filename": "SPIDERMAN.2002.TRUEFRENCH.BDRIP.XviD.AC3.avi"}}
r = vb.parse_stream(torrent, "Spider-Man")
check(r is not None and r["link"] == play(1), "flux torrent reconnu, lien conserve")
check(r["model_type"] == "direct" and r["cache_status"] == "cached" and r["source"] == "Viewio", "type direct, deja en cache, source Viewio")
check(r["quality"] not in ("Unknown", None, ""), "qualite extraite : %r" % r["quality"])
check("GB" in str(r["size"]) and str(r["size"]).startswith("0.7"), "taille lue dans videoSize : %r" % r["size"])
check("Spider-Man (2002)" in r["display_name"], "folderName utilise comme nom : %r" % r["display_name"])
r2 = vb.parse_stream(ddl, "Spider-Man")
check(r2 is not None and "TRUEFRENCH" in r2["display_name"], "lien direct (filename) conserve")
check(vb.parse_stream({**torrent, "behaviorHints": {**torrent["behaviorHints"], "cached": False}, "name": "[AllDebrid] Viewio"}, "t") is None, "flux non cache ecarte")
check(vb.parse_stream({"name": "x", "url": "https://autre.site/playback/zzz", "behaviorHints": {"cached": True}}, "t") is None, "URL hors Viewio ignoree")
check(vb.parse_stream({"name": "x", "url": "https://addon.viewio.me/autre/zzz", "behaviorHints": {"cached": True}}, "t") is None, "chemin hors /playback ignore")
mismatch = {"name": "⚡ Viewio", "url": play(3), "behaviorHints": {"cached": True, "filename": "Solo.Leveling.S02E05.1080p.WEB.mkv"}}
check(vb.parse_stream(mismatch, "Solo Leveling", "1", "8") is None, "garde-fou episode : S02E05 refuse pour S01E08")
check(vb.parse_stream(mismatch, "Solo Leveling", "2", "5") is not None, "episode demande conserve")
vostfr = {"name": "⚡ Viewio", "url": play(4), "behaviorHints": {"cached": True, "bingeGroup": "viewio|opaque|viewio%7Cddl%7C1080p%7Cvostfr%7Ckitsu", "filename": "Anime.S01E01.1080p.mkv"}}
check("VOSTFR" in vb.parse_stream(vostfr, "Anime", "1", "1")["display_name"].upper(), "VOSTFR conserve dans le nom")

# --- 2. liens de lecture --------------------------------------------------
check(vb.is_play_link(play(5)) and not vb.is_play_link("https://autre.site/playback/x") and not vb.is_play_link("magnet:?xt=urn:btih:abc"), "is_play_link : meme hote + /playback/ seulement")
settings.VIEWIO_MANIFEST_URL = ""
check(not vb.is_play_link(play(5)), "is_play_link faux quand la source est desactivee")
check(asyncio.run(vb.ViewioScraper().search("t", None, {"imdb_id": "tt1"})) == [], "source desactivee : aucune requete, liste vide")
settings.VIEWIO_MANIFEST_URL = "https://addon.viewio.me/u/test-jeton"
check(vb._manifest_base() == "https://addon.viewio.me/u/test-jeton", "URL de base : manifest.json facultatif")
settings.VIEWIO_MANIFEST_URL = "https://addon.viewio.me/u/test-jeton/manifest.json"

# --- 3. verification du cache : les flux directs ne vont pas a AllDebrid ---
class FakeDebrid:
    calls = []
    async def check_cache_and_enrich(self, res, key, cfg, t, s, e, hosts):
        FakeDebrid.calls.append([x.get("source") for x in res])
        for x in res: x["cache_status"] = "uncached"
        return res
    def is_dead_link_result_authoritative(self, r): return False
svc = st.stream_service
svc._get_debrid_service = lambda name: FakeDebrid()
res_in = [dict(r, link=play(6)), {"link": "magnet:?xt=urn:btih:abc", "source": "C411", "model_type": "torrent", "quality": "1080p", "language": "French", "size": "1 GB", "display_name": "x"}]
out = asyncio.run(svc._check_cache_and_enrich(res_in, [{"service": "alldebrid", "api_key": "k"}], {}, 5.0))
check(all("Viewio" not in c for c in FakeDebrid.calls), "AllDebrid ne recoit aucun flux Viewio : %r" % FakeDebrid.calls)
vv = [x for x in out if x.get("source") == "Viewio"]
check(len(vv) == 1 and vv[0]["cache_status"] == "cached" and vv[0]["debrid_service"] == "viewio", "flux Viewio conserve, ⚡, service viewio")
check(len([x for x in out if x.get("source") == "C411"]) == 1, "les autres sources passent toujours par AllDebrid")
out2 = asyncio.run(svc._check_cache_and_enrich(res_in, [{"service": "alldebrid", "api_key": "k"}, {"service": "alldebrid", "api_key": "k2"}], {}, 5.0))
check(len([x for x in out2 if x.get("source") == "Viewio"]) == 1, "pas de doublon avec deux services debrid")

# --- 4. lecture : le lien Viewio est transmis tel quel ---------------------
check(asyncio.run(svc.resolve_link(play(7), {})) == play(7), "resolve_link renvoie le lien Viewio tel quel")

print("\n%d echec(s)" % len(fails))
raise SystemExit(1 if fails else 0)
