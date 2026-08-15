import re


# ===========================
# Languages Dictionary
# ===========================
LANGUAGES = {
    "Arab": ["arab", "ar", "ara"],
    "Bengali": ["bengali", "bn", "ben"],
    "Chinese": ["chinese", "zh", "cn", "chi"],
    "English": ["english", "en", "eng", "vosten"],
    "French": ["french", "vf", "vff", "vof", "vostfr", "subfrench", "truefrench", "fr", "fra", "fre", "vfi", "vf2"],
    "French (Canada)": ["french (canada)", "vfq"],
    "FRENCH AD": ["french ad"],
    "German": ["german", "de", "ger", "deu"],
    "Hindi": ["hindi", "hi", "hin"],
    "Italian": ["italian", "it", "ita"],
    "Japanese": ["japanese", "ja", "jp", "jpn"],
    "Korean": ["korean", "ko", "kor"],
    "Mandarin": ["mandarin", "cmn"],
    "Portuguese": ["portuguese", "pt", "por"],
    "Russian": ["russian", "ru", "rus"],
    "Spanish": ["spanish", "es", "esp", "spa"],
    "Turkish": ["turkish", "tr", "tur"],
    "Danish": ["danish", "da", "dan"],
    "Finnish": ["finnish", "fi", "fin"],
    "Swedish": ["swedish", "sv", "swe"],
    "Bulgarian": ["bulgarian", "bulgare", "bg", "bul"],
    "Dutch": ["dutch", "nl", "nld"],
    "Persian": ["persian", "fa", "per"],
    "Indonesian": ["indonesian", "id", "ind"],
    "Hebrew": ["hebrew", "he", "heb"],
    "Thai": ["thai", "th", "tha"],
    "Czech": ["czech", "cz", "cs", "cze"],
    "Albanian": ["albanian", "sq", "alb"],
    "Greek": ["greek", "el", "gre"],
    "Hungarian": ["hungarian", "hu", "hun"],
    "Malaysian": ["malaysian"],
    "Norwegian": ["norwegian", "no", "nor"],
    "Norwegian Bokmål": ["norwegian bokmål"],
    "Polish": ["polish", "pl", "pol"],
    "Lithuanian": ["lithuanian", "lt", "lit"],
    "Croatian": ["croatian", "hr", "hrv"],
    "Malay": ["malay", "ms", "msa"],
    "Romanian": ["romanian", "ro", "ron"],
    "Ukrainian": ["ukrainian", "uk", "ukr"],
    "Vietnamese": ["vietnamese", "vi", "vie"],
    "Sámegiella": ["sámegiella"],
    "Muet": ["muet"],
    "Georgian": ["georgian", "ka", "geo"],
    "Nigerian": ["nigerian"],
    "Maasai": ["maasai"],
    "Estonian": ["estonian", "et", "est"],
    "Serbian": ["serbian", "sr", "srp"],
    "Slovak": ["slovak", "sk", "slk"],
    "Slovenian": ["slovenian", "sl", "slv"],
    "Amharic": ["amharic", "am", "amh"],
    "Belarusian": ["belarusian", "be", "bel"],
    "Bosnian": ["bosnian", "bs", "bos"],
    "Burmese": ["burmese", "myanmar", "my", "mya"],
    "Dzongkha": ["dzongkha", "dz", "dzo"],
    "Icelandic": ["icelandic", "is", "ice"],
    "Kazakh": ["kazakh", "kk", "kaz"],
    "Kurdish": ["kurdish", "ku", "kur"],
    "Latin": ["latin", "la", "lat"],
    "Latvian": ["latvian", "lv", "lav"],
    "Macedonian": ["macedonian", "mk", "mac"],
    "Maori": ["maori", "mi", "mao"],
    "Mongolian": ["mongolian", "mn", "mon"],
    "Serbo-Croatian": ["serbo-croatian"],
    "Tagalog": ["tagalog", "tl", "tgl"],
    "Tibetan": ["tibetan", "bo", "tib"],
    "Walloon": ["walloon", "wa", "wln"],
    "Wolof": ["wolof", "wo", "wol"],
    "Yoruba": ["yoruba", "yo", "yor"],
    "Moore": ["moore", "mos"],
    "Quechuan": ["quechuan", "qu", "que"],
    "Rwanda": ["rwanda", "rw", "kin"],
    "Filipino": ["filipino", "fil"],
    "Afrikaans": ["afrikaans", "af", "afr"],
    "Créole": ["créole"],
    "Haitian Creole": ["haitian creole", "ht"],
    "Gujarati": ["gujarati", "gu", "guj"],
    "Cantonese": ["cantonese", "yue"],
    "Armenian": ["armenian", "hy", "arm"],
    "Azerbaijani": ["azerbaijani", "az", "aze"],
    "Basque": ["basque", "eu", "baq"],
    "Catalan": ["catalan", "ca", "cat"],
    "Cebuano": ["cebuano", "ceb"],
    "Chichewa": ["chichewa", "ny", "nya"],
    "Corsican": ["corsican", "co", "cos"],
    "Esperanto": ["esperanto", "eo", "epo"],
    "Frisian": ["frisian", "fy", "fry"],
    "Galician": ["galician", "gl", "glg"],
    "Hausa": ["hausa", "ha", "hau"],
    "Hawaiian": ["hawaiian", "haw"],
    "Igbo": ["igbo", "ig", "ibo"],
    "Irish": ["irish", "ga", "gle"],
    "Javanese": ["javanese", "jv", "jav"],
    "Kannada": ["kannada", "kn", "kan"],
    "Khmer": ["khmer", "km", "khm"],
    "Kyrgyz": ["kyrgyz", "ky", "kir"],
    "Lao": ["lao", "lo"],
    "Luxembourgish": ["luxembourgish", "lb", "ltz"],
    "Malagasy": ["malagasy", "mg", "mlg"],
    "Maltese": ["maltese", "mt", "mlt"],
    "Marathi": ["marathi", "mr", "mar"],
    "Nepali": ["nepali", "ne", "nep"],
    "Pashto": ["pashto", "ps", "pus"],
    "Punjabi": ["punjabi", "pa", "pan"],
    "Sindhi": ["sindhi", "sd", "snd"],
    "Sinhala": ["sinhala", "si", "sin"],
    "Somali": ["somali", "so", "som"],
    "Swahili": ["swahili", "sw", "swa"],
    "Tajik": ["tajik", "tg", "tgk"],
    "Tamil": ["tamil", "ta", "tam"],
    "Telugu": ["telugu", "te", "tel"],
    "Uzbek": ["uzbek", "uz", "uzb"],
    "Welsh": ["welsh", "cy", "wel"],
    "Xhosa": ["xhosa", "xh", "xho"],
    "Yiddish": ["yiddish", "yi", "yid"],
    "Zulu": ["zulu", "zu", "zul"],
    "VO": ["vo"],
    "Multi": ["multi"],
    "Unknown": ["unknown"],
}


# ===========================
# Reverse Language Mapping
# ===========================
LANGUAGE_MAPPING = {}
for standard_lang, variants in LANGUAGES.items():
    for variant in variants:
        LANGUAGE_MAPPING[variant.lower()] = standard_lang


# ===========================
# Release Language Markers
# ===========================
RELEASE_LANGUAGE_MARKERS = (
    "MULTI", "TRUEFRENCH", "FRENCH", "VFF", "VFQ", "VFI", "VF2", "VF",
    "VOSTFR", "VOSTEN", "SUBFRENCH", "VOF", "VO", "MUET",
)
RELEASE_LANGUAGE_RE = re.compile(
    r"\b(" + "|".join(RELEASE_LANGUAGE_MARKERS) + r")\b", re.IGNORECASE
)


# ===========================
# Available Languages
# ===========================
AVAILABLE_LANGUAGES = sorted(list(LANGUAGES.keys()))


# ===========================
# Multi-Language Constants
# ===========================
MULTI_LANGUAGE_PREFIX = "multi ("
MULTI_PREFIX_LENGTH = len(MULTI_LANGUAGE_PREFIX)


# ===========================
# Language Normalization
# ===========================
def normalize_language(raw_language: str) -> str:
    if not raw_language:
        return "Unknown"

    normalized = raw_language.lower().strip()

    if normalized.upper() in ["N/A", "NULL", "UNKNOWN", "INCONNU", ""]:
        return "Unknown"

    if normalized.startswith(MULTI_LANGUAGE_PREFIX) and normalized.endswith(")"):
        inner_lang = normalized[MULTI_PREFIX_LENGTH:-1].strip()
        mapped_inner = LANGUAGE_MAPPING.get(inner_lang)
        if mapped_inner:
            return mapped_inner

    if normalized.startswith("multi") and any(sep in normalized for sep in [".", "-", "_", " "]):
        parts = re.split(r"[.\-_ ]", normalized)
        mapped_langs = []
        for part in parts:
            if part in ("multi", ""):
                continue
            mapped = LANGUAGE_MAPPING.get(part)
            if mapped and mapped not in mapped_langs:
                mapped_langs.append(mapped)
        if len(mapped_langs) > 1:
            return f"Multi ({', '.join(mapped_langs)})"
        if len(mapped_langs) == 1:
            return mapped_langs[0]

    mapped = LANGUAGE_MAPPING.get(normalized)
    if mapped:
        return mapped

    return "Unknown"


# ===========================
# Free-Text Language Extraction
# ===========================
def extract_release_language(text: str, fallback: str = "") -> dict:
    raw_language = "Unknown"
    for value in (text or "", fallback or ""):
        match = RELEASE_LANGUAGE_RE.search(value)
        if match:
            raw_language = match.group(1).upper()
            break

    return {
        "language": normalize_language(raw_language),
        "raw_language": raw_language,
    }


# ===========================
# Tokenized Language Extraction
# ===========================
# `LANGUAGE_MAPPING` couvre ~130 langues via des codes ISO à 2-3 lettres.
# Un token à 2 lettres a une chance élevée de coïncider avec un mot FR/EN
# courant OU une syllabe romanisée japonaise ("no", "de", "la", "en", "it",
# "na", "wa", "ka", "ta"...) sans le moindre rapport avec une langue. Confirmé
# en conditions réelles (2026-08-01) : "Kimi No Na Wa" matche successivement
# "no" (Norwegian) PUIS "wa" (Walloon) une fois "no" écarté — whack-a-mole
# caractéristique des titres romanisés japonais, qui contiennent presque
# toujours une syllabe de 2 lettres qui coïncide avec un code ISO quelque part
# dans une liste de 130 langues. Un token à 3 lettres est bien moins exposé,
# mais pas totalement ("cat", "fin", "ben" sont aussi des mots/prénoms usuels).
#
# Stratégie : plutôt qu'une liste noire qui s'allonge indéfiniment à chaque
# nouvelle collision découverte, un token à 2 lettres n'est accepté que s'il
# fait partie d'un petit vocabulaire scène explicitement utilisé ici (VF/VO) ;
# les langues à code 2 lettres restent détectables via leur forme longue
# ("norwegian", "german"...) ou leur code 3 lettres, non ambigus.
#
# ⚠️ Absent d'upstream (vérifié encore en 3.8.2 : « et » → Estonian, « de » →
# German, « fin » → Finnish) — garde-fou maison, à conserver lors des
# remontées de version. Placé ici plutôt que dans helpers.py pour que TOUTES
# les sources en bénéficient, y compris celles d'upstream.
_SAFE_SHORT_CODES = {"vf", "vo"}
_AMBIGUOUS_3LETTER_CODES = {"cat", "fin", "ben"}


def _is_plausible_language_token(token: str) -> bool:
    if len(token) == 2:
        return token in _SAFE_SHORT_CODES
    if token in _AMBIGUOUS_3LETTER_CODES:
        return False
    return True


def extract_language_from_tokens(tokens: list) -> str:
    for token in tokens:
        if not _is_plausible_language_token(token):
            continue
        language = LANGUAGE_MAPPING.get(token)
        if language and language != "Unknown":
            return language
    return "Unknown"


def extract_raw_language_from_tokens(tokens: list) -> str:
    for token in tokens:
        if not _is_plausible_language_token(token):
            continue
        language = LANGUAGE_MAPPING.get(token)
        if language and language != "Unknown":
            return token.upper()
    return "Unknown"


# ===========================
# Language Combination
# ===========================
def combine_languages(audio_langs: list, subtitle_langs: list, user_prefs: list = None) -> str:
    normalized_audio = []
    for lang in audio_langs:
        if lang:
            normalized = normalize_language(lang)
            normalized_audio.append(normalized)

    normalized_subs = []
    for lang in subtitle_langs:
        if lang:
            normalized = normalize_language(lang)
            normalized_subs.append(normalized)

    all_langs = []
    for lang in normalized_audio:
        if lang not in all_langs:
            all_langs.append(lang)
    for lang in normalized_subs:
        if lang not in all_langs:
            all_langs.append(lang)

    if user_prefs and "Unknown" not in user_prefs:
        all_langs = [lang for lang in all_langs if lang != "Unknown"]

    if len(all_langs) == 0:
        return "Unknown"

    if len(all_langs) == 1 and all_langs[0] == "Multi":
        return "Multi"

    if len(all_langs) == 1:
        return all_langs[0]

    if len(all_langs) > 1:
        if user_prefs:
            filtered_langs = [lang for lang in all_langs if lang in user_prefs]
            if filtered_langs:
                return f"Multi ({', '.join(filtered_langs)})"
            return f"Multi ({', '.join(all_langs)})"

        return f"Multi ({', '.join(all_langs)})"

    return "Unknown"


# ===========================
# Raw Language Combination
# ===========================
def combine_raw_languages(audio_langs: list, subtitle_langs: list) -> str:
    all_raw = []
    for lang in audio_langs:
        if lang and lang not in all_raw:
            all_raw.append(lang)
    for lang in subtitle_langs:
        if lang and lang not in all_raw:
            all_raw.append(lang)

    if len(all_raw) == 0:
        return "Unknown"
    if len(all_raw) == 1:
        return all_raw[0]
    return "MULTi." + ".".join(all_raw)
