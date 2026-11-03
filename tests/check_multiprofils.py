# Test d integration du multi-utilisateurs : deux utilisateurs sur une base temporaire.
# Usage : SECRET_KEY=... python tests/check_multiprofils.py
import asyncio, os, tempfile, json
d = tempfile.mkdtemp()
os.environ.update(SECRET_KEY="x"*40, DATABASE_PATH=f"{d}/t.db", DATABASE_TYPE="sqlite")
import httpx
from wastream.utils.database import setup_database
from wastream.services.users import create_user, get_user_config_from_url
from wastream.utils.profiles import build_addon_urls
from wastream.utils import tracker_keys as tk
from wastream.services import torrent_files as tf
from wastream.main import app

ok = bad = 0
def check(c, m):
    global ok, bad
    if c: ok += 1
    else: bad += 1; print("ECHEC:", m)

async def main():
    await setup_database()
    cfg = {"profile_id": "papa", "profile_name": "Papa", "default_services": ["tmdb"],
           "debrid_services": [{"service": "alldebrid", "api_key": "cle-papa"}],
           "tmdb_api_token": "tmdb-papa", "languages": ["French"],
           "tracker_api_keys": {"c411": "ygg-papa"},
           "profiles": [{"id": "lea", "name": "Lea", "debrid_services": [{"service": "torbox", "api_key": "cle-lea"}],
                         "tracker_api_keys": {"c411": "ygg-lea"}}]}
    r = await create_user("motdepasse", cfg)
    uid = r["uuid"]
    urls = {u["id"]: u["url"] for u in build_addon_urls("http://t", uid, "motdepasse", cfg)}
    legacy_cfg = {"debrid_services": [{"service": "alldebrid", "api_key": "cle-old"}], "tmdb_api_token": "t"}
    r2 = await create_user("ancien", legacy_cfg)
    legacy_url = build_addon_urls("http://t", r2["uuid"], "ancien", legacy_cfg)[0]["url"]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        get = lambda u: c.get(u.replace("http://t", ""))
        a = await get(urls["papa"]); l = await get(urls["lea"]); old = await get(legacy_url)
        check(a.status_code == 200 and "Papa" in a.json()["name"], f"manifest papa {a.status_code}")
        check(l.status_code == 200 and "Lea" in l.json()["name"], f"manifest lea {l.status_code}")
        check(old.status_code == 200, f"ancien compte sans profil {old.status_code}")
        # jeton de Lea sur l'URL de Papa, et inversement
        tok_l = urls["lea"].split("/")[-2]; tok_p = urls["papa"].split("/")[-2]
        x = await c.get(f"/{uid}/papa/{tok_l}/manifest.json"); y = await c.get(f"/{uid}/lea/{tok_p}/manifest.json")
        z = await c.get(f"/{uid}/inconnu/{tok_l}/manifest.json")
        check(x.status_code == 401, f"jeton Lea sur URL Papa -> {x.status_code}")
        check(y.status_code == 401, f"jeton Papa sur URL Lea -> {y.status_code}")
        check(z.status_code == 401, f"profil inconnu -> {z.status_code}")
        # jeton historique (sans profil) sur URL de profil
        old_style = (await get_user_config_from_url(uid, tok_p, "papa")) is not None
        check(old_style, "config papa lisible avec son jeton")
    ep = await get_user_config_from_url(uid, tok_p, "papa"); el = await get_user_config_from_url(uid, tok_l, "lea")
    blob_l = json.dumps(el); blob_p = json.dumps(ep)
    check("cle-papa" not in blob_l and "ygg-papa" not in blob_l, "config de Lea ne contient rien de Papa")
    check("cle-lea" not in blob_p and "ygg-lea" not in blob_p, "config de Papa ne contient rien de Lea")
    check(el.get("tmdb_api_token") == "tmdb-papa", "TMDB partage par defaut pour Lea")
    # cloisonnement des .torrent par cle
    tk.set_request_tracker_owners(ep)
    _, own_p = tk.resolve_tracker_key(ep, "c411"); _, own_l = tk.resolve_tracker_key(el, "c411")
    check(own_p != own_l and own_p != tk.GLOBAL_OWNER, "proprietaires de cle distincts")
    h = "ab"*20
    tf.remember_torrent_url(h, "C411", "https://t/dl?passkey=PAPA", own_p)
    tk.set_request_tracker_owners(ep); check(tf._lookup(h) is not None, "Papa retrouve son .torrent")
    tk.set_request_tracker_owners(el); check(tf._lookup(h) is None, "Lea ne retrouve pas le .torrent de Papa")
    check(tk.tracker_cache_name("c411", ep) != tk.tracker_cache_name("c411", el), "caches de resultats distincts")
    print(f"{ok} verifications OK, {bad} echec(s)")
asyncio.run(main())
