"""Tests pour l'auto-decouverte des identifiants exacts (imdbid/tmdbid/tvdbid)
supportes par un tracker Torznab via son endpoint ?t=caps (cf. base.py).
"""
from wastream.scrapers.torznab.base import _parse_caps_id_params

_C411_CAPS = b"""<?xml version="1.0" encoding="UTF-8"?>
<caps>
  <searching>
    <search available="yes" supportedParams="q" />
    <tv-search available="yes" supportedParams="q,season,ep,tmdbid,imdbid" />
    <movie-search available="yes" supportedParams="q,imdbid,tmdbid" />
  </searching>
</caps>
"""

_YGGREBORN_CAPS = b"""<?xml version="1.0" encoding="UTF-8"?>
<caps>
  <searching>
    <search available="yes" supportedParams="q,cat,limit,offset" />
    <tv-search available="yes" supportedParams="q,season,ep,cat,limit,offset" />
    <movie-search available="yes" supportedParams="q,cat,limit,offset" />
  </searching>
</caps>
"""

_UNAVAILABLE_MOVIE_CAPS = b"""<?xml version="1.0" encoding="UTF-8"?>
<caps>
  <searching>
    <movie-search available="no" supportedParams="q,imdbid" />
    <tv-search available="yes" supportedParams="q,tvdbid" />
  </searching>
</caps>
"""


def test_extracts_known_id_params_in_declared_order():
    movie_params, tv_params = _parse_caps_id_params(_C411_CAPS)
    assert movie_params == ["imdbid", "tmdbid"]
    assert tv_params == ["tmdbid", "imdbid"]


def test_no_id_params_supported_returns_empty_lists():
    movie_params, tv_params = _parse_caps_id_params(_YGGREBORN_CAPS)
    assert movie_params == []
    assert tv_params == []


def test_available_no_is_ignored_even_if_params_listed():
    movie_params, tv_params = _parse_caps_id_params(_UNAVAILABLE_MOVIE_CAPS)
    assert movie_params == []
    assert tv_params == ["tvdbid"]


def test_malformed_xml_returns_empty_lists():
    movie_params, tv_params = _parse_caps_id_params(b"not valid xml <<<")
    assert movie_params == []
    assert tv_params == []


def test_missing_searching_node_returns_empty_lists():
    movie_params, tv_params = _parse_caps_id_params(b"<caps></caps>")
    assert movie_params == []
    assert tv_params == []
