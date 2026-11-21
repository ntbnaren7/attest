"""
Script and language detection module using Unicode ranges.
Supports all 22 Scheduled Indian Languages + English.
"""

from collections import Counter
from typing import Dict, Any, List

UNICODE_SCRIPTS = {
    "Tamil": {
        "ranges": [(0x0B80, 0x0BFF)],
        "languages": ["Tamil"],
    },
    "Telugu": {
        "ranges": [(0x0C00, 0x0C7F)],
        "languages": ["Telugu"],
    },
    "Kannada": {
        "ranges": [(0x0C80, 0x0CFF)],
        "languages": ["Kannada"],
    },
    "Malayalam": {
        "ranges": [(0x0D00, 0x0D7F)],
        "languages": ["Malayalam"],
    },
    "Devanagari": {
        "ranges": [(0x0900, 0x097F)],
        "languages": ["Hindi", "Marathi", "Sanskrit", "Konkani", "Maithili", "Bodo", "Dogri", "Nepali", "Sindhi"],
    },
    "Bengali": {
        "ranges": [(0x0980, 0x09FF)],
        "languages": ["Bengali", "Assamese"],
    },
    "Gujarati": {
        "ranges": [(0x0A80, 0x0AFF)],
        "languages": ["Gujarati"],
    },
    "Gurmukhi": {
        "ranges": [(0x0A00, 0x0A7F)],
        "languages": ["Punjabi"],
    },
    "Odia": {
        "ranges": [(0x0B00, 0x0B7F)],
        "languages": ["Odia"],
    },
    "Arabic": {
        "ranges": [(0x0600, 0x06FF), (0x0750, 0x077F), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF)],
        "languages": ["Urdu", "Kashmiri", "Sindhi"],
    },
    "Ol Chiki": {
        "ranges": [(0x1C50, 0x1C7F)],
        "languages": ["Santali"],
    },
    "Meetei Mayek": {
        "ranges": [(0xABC0, 0xABFF), (0xAAE0, 0xAAFF)],
        "languages": ["Manipuri"],
    },
    "Latin": {
        "ranges": [(0x0041, 0x005A), (0x0061, 0x007A)],
        "languages": ["English"],
    },
}


def count_letters(text: str) -> int:
    return sum(1 for char in text if char.isalpha())


def detect_scripts(text: str) -> Dict[str, Any]:
    """
    Detect Unicode scripts present in the text and calculate character counts & proportions.
    Numbers, punctuation, and formatting symbols are excluded to avoid script contamination.
    """
    counts = Counter()
    total_alpha = 0

    for char in text:
        if not char.isalpha():
            continue

        codepoint = ord(char)
        matched = False

        for script_name, config in UNICODE_SCRIPTS.items():
            for start, end in config["ranges"]:
                if start <= codepoint <= end:
                    counts[script_name] += 1
                    total_alpha += 1
                    matched = True
                    break
            if matched:
                break

        if not matched:
            counts["Other"] += 1
            total_alpha += 1

    result = {}
    for script_name, count in counts.items():
        if count > 0:
            ratio = round(count / total_alpha, 4) if total_alpha else 0.0
            result[script_name] = {
                "characters": count,
                "ratio": ratio,
            }

    return {
        "scripts": result,
        "total_characters": total_alpha,
    }


def map_scripts_to_languages(
    detected_scripts: Dict[str, Any],
    lid_language_code: str = None
) -> List[str]:
    """
    Infer candidate languages from detected scripts.
    Script detection identifies alphabet families (e.g. Devanagari, Bengali).
    If Sarvam LID identified a specific BCP-47 language code, that is prioritized.
    """
    languages = []
    
    # BCP-47 mapping if Sarvam LID succeeded
    bcp47_map = {
        "hi-IN": "Hindi",
        "ta-IN": "Tamil",
        "te-IN": "Telugu",
        "kn-IN": "Kannada",
        "ml-IN": "Malayalam",
        "bn-IN": "Bengali",
        "gu-IN": "Gujarati",
        "mr-IN": "Marathi",
        "pa-IN": "Punjabi",
        "or-IN": "Odia",
        "ur-IN": "Urdu",
        "as-IN": "Assamese",
        "en-IN": "English",
    }

    # Check if any Indic script was detected
    has_indic_script = any(s != "Latin" and s != "Other" for s in detected_scripts)

    # Only trust LID if genuine Indic script exists or LID indicates English
    if has_indic_script and lid_language_code and lid_language_code in bcp47_map:
        cand = bcp47_map[lid_language_code]
        if cand != "English":
            languages.append(cand)

    # Add languages based on scripts with significant presence (ratio >= 5%)
    for script_name, data in detected_scripts.items():
        if script_name in UNICODE_SCRIPTS and data.get("ratio", 0) >= 0.05:
            script_langs = UNICODE_SCRIPTS[script_name]["languages"]
            for lang in script_langs:
                if lang not in languages:
                    languages.append(lang)

    # If purely Latin text, ensure English is primary
    if not has_indic_script and "Latin" in detected_scripts:
        if "English" not in languages:
            languages.insert(0, "English")

    if not languages and detected_scripts:
        languages = ["Unknown"]

    return languages
