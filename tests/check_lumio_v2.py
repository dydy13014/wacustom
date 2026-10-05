"""Vérifications autonomes (sans pytest) de l'adaptation Lumio v2.
Lancer : python tests/check_lumio_v2.py (variables d'environnement de test requises)."""
import asyncio, os
from wastream.config.settings import settings
settings.LUMIO_MANIFEST_ID = "test-id"
from wastream.scrapers.lumio import base as lb
import wastream.services.stream as st

TOK = "A" * 43
def play(i=0): return lb.PLAY_PREFIX + TOK[:-1] + str(i)
fails = []
def check(cond, msg):
    print(("OK    " if cond else "ECHEC ") + msg)
    if not cond: fails.append(msg)

# --- 1. parseur v2 sur la structure réellement observée --------------------
bonus = {"name": "BluRay", "description": "1080p • 390 Mo", "url": play(1),
         "behaviorHints": {"notWebReady": True, "bingeGroup": "lumio|3|vostfr|",
                           "filename": "La création du monde des Fremen.mkv", "videoSize": 390256050}}
sp = {"name": "WEBRip", "description": "1080p • 379 Mo", "url": play(2),
      "behaviorHints": {"notWebReady": True, "bingeGroup": "lumio|3|fr|", "videoSize": 379475356}}
r = lb.parse_v2_stream(sp, "South Park", "24", "1")
check(r is not None and r["link"] == play(2), "flux v2 reconnu, lien conservé")
check(r["model_type"] == "direct" and r["cache_status"] == "cached" and r["source"] == "Lumio", "type direct, déjà en cache, source Lumio")
check(r["quality"] not in ("Unknown", None, ""), "qualité extraite : %r" % r["quality"])
check(r["language"] in ("French", "Multi (VFF)") or "French" in str(r["language"]), "langue déduite du bingeGroup fr : %r" % r["language"])
check(str(r["size"]).startswith("0.35") and "GB" in str(r["size"]), "taille lue dans videoSize (379 475 356 octets) : %r" % r["size"])
check(r["season"] == "24" and r["episode"] == "1", "saison/épisode renseignés")
rb = lb.parse_v2_stream(bonus, "Dune : Deuxième Partie")  # année inconnue : pas de filtre
check(lb.parse_v2_stream(bonus, "Dune : Deuxième Partie", None, None, "2024") is None, "bonus de film (année absente du nom) écarté")
film = {"name": "WEBRip", "description": "1080p", "url": play(9), "behaviorHints": {"bingeGroup": "lumio|3|fr|", "filename": "Dune.Part.Two.2024.TRUEFRENCH.WEBRip.x264-ALFA.mkv"}}
check(lb.parse_v2_stream(film, "Dune : Deuxième Partie", None, None, "2024") is not None, "vraie release (année présente) conservée")
check(lb.parse_v2_stream(bonus, "South Park", "24", "1", "1997") is not None, "le filtre année ne s'applique pas aux séries")
check(rb is not None and "Fremen" in rb["display_name"], "nom de fichier conservé (bonus identifiable)")
check("VOSTFR" in rb["display_name"].upper(), "VOSTFR conservé dans le nom : %r" % rb["display_name"])
check(lb.parse_v2_stream({"name": "x", "url": "https://autre.site/play/zzz"}, "t") is None, "URL hors Lumio ignorée")
check(lb.parse_v2_stream({"name": "x", "url": lb._BASE_URL + "/playback/abc.def"}, "t") is None, "ancien format v1 laissé à l'ancien code")
mismatch = {"name": "WEBRip", "description": "1080p", "url": play(3), "behaviorHints": {"filename": "Solo.Leveling.S02E05.1080p.WEB.mkv"}}
check(lb.parse_v2_stream(mismatch, "Solo Leveling", "1", "8") is None, "garde-fou épisode : S02E05 refusé pour S01E08")

# --- 2. vérification du cache : les flux directs ne vont pas à AllDebrid -----
class FakeDebrid:
    calls = []
    async def check_cache_and_enrich(self, res, key, cfg, t, s, e, hosts):
        FakeDebrid.calls.append([x.get("source") for x in res])
        for x in res: x["cache_status"] = "uncached"
        return res
    def is_dead_link_result_authoritative(self, r): return False
svc = st.stream_service
svc._get_debrid_service = lambda name: FakeDebrid()
res_in = [dict(r, link=play(5)), {"link": "magnet:?xt=urn:btih:abc", "source": "C411", "model_type": "torrent", "quality": "1080p", "language": "French", "size": "1 GB", "display_name": "x"}]
out = asyncio.run(svc._check_cache_and_enrich(res_in, [{"service": "alldebrid", "api_key": "k"}], {}, 5.0))
check(all("Lumio" not in c for c in FakeDebrid.calls), "AllDebrid ne reçoit aucun flux Lumio : %r" % FakeDebrid.calls)
lum = [x for x in out if x.get("source") == "Lumio"]
check(len(lum) == 1 and lum[0]["cache_status"] == "cached" and lum[0]["debrid_service"] == "lumio", "flux Lumio conservé, ⚡, service lumio")
check(len([x for x in out if x.get("source") == "C411"]) == 1, "les autres sources passent toujours par AllDebrid")
# deux services debrid => pas de doublon Lumio
out2 = asyncio.run(svc._check_cache_and_enrich(res_in, [{"service": "alldebrid", "api_key": "k"}, {"service": "alldebrid", "api_key": "k2"}], {}, 5.0))
check(len([x for x in out2 if x.get("source") == "Lumio"]) == 1, "pas de doublon avec deux services debrid")

# --- 3. lecture : le lien Lumio est retransmis tel quel ----------------------
got = asyncio.run(svc.resolve_link(play(7), {}, None, None, "lumio"))
check(got == play(7), "resolve_link renvoie le lien /play sans appeler de service debrid")
resp = svc._build_link_response(got)
check(getattr(resp, "status_code", None) == 302 and resp.headers.get("location") == play(7), "réponse HTTP : 302 vers le lien Lumio")

# --- 4. affichage Stremio ----------------------------------------------------
async def _nodead(_): return {}
st.get_dead_link_statuses_batch = _nodead
streams = asyncio.run(svc._format_streams(lum, {"_user_uuid": "u", "_enc_password": "p"}, "https://example.test", "24", "1", None, "series"))
check(len(streams) == 1 and "/playback/" in streams[0]["url"], "flux Stremio construit avec une URL /playback/")
check("⚡" in streams[0]["name"], "badge ⚡ affiché : %r" % streams[0]["name"].replace("\n", " "))
from wastream.utils.helpers import decode_playback_token
tok = streams[0]["url"].split("/playback/")[1].split("/")[0]
d = decode_playback_token(tok)
check(d and d.get("l") == play(2) or d.get("l") == play(5) or d.get("l", "").startswith(lb.PLAY_PREFIX), "le jeton de lecture contient le lien Lumio")
print("\n%d échec(s)" % len(fails))
raise SystemExit(1 if fails else 0)
