# SPDX-FileCopyrightText: 2026 Malte Dreyer
# SPDX-License-Identifier: MIT
"""
Texts that are sent to the model, per prompt language.

translation.prompt_language selects the language of the instructions
("de", the established default, or "en"). It is independent of the interface
language. tools/prompt_eval.py compares both on a reference set with your
model.
"""

from typing import Dict

PROMPT_LANGUAGES = ("de", "en")

PROMPT_FILES = {
    "de": "prompts/base_translation.txt",
    "en": "prompts/base_translation_en.txt",
}

LANGUAGE_NAMES: Dict[str, Dict[str, str]] = {
    "de": {
        'de': 'Deutsch', 'en': 'Englisch', 'fr': 'Französisch', 'es': 'Spanisch',
        'it': 'Italienisch', 'pt': 'Portugiesisch', 'nl': 'Niederländisch',
        'pl': 'Polnisch', 'ru': 'Russisch', 'zh': 'Chinesisch', 'ja': 'Japanisch',
        'ko': 'Koreanisch',
    },
    "en": {
        'de': 'German', 'en': 'English', 'fr': 'French', 'es': 'Spanish',
        'it': 'Italian', 'pt': 'Portuguese', 'nl': 'Dutch', 'pl': 'Polish',
        'ru': 'Russian', 'zh': 'Chinese', 'ja': 'Japanese', 'ko': 'Korean',
    },
}

TEXTS: Dict[str, Dict[str, str]] = {
    "de": {
        "auto_source": "der Ausgangssprache des Textes (erkenne sie selbst)",
        "glossary_header": "GLOSSAR:",
        "glossary_case": "Beachte die Groß-/Kleinschreibung der Quellbegriffe exakt.",
        "context_header": "KONTEXT:",
        "user_message": "Bitte übersetze folgenden Text:",
        "formal_de": 'Sie-Form (formell, höfliche Ansprache mit "Sie/Ihr/Ihnen")',
        "informal_de": 'Du-Form (informell, persönliche Ansprache mit "du/dein/dir")',
        "formal_other": "Formal register (polite, professional tone)",
        "informal_other": "Informal register (casual, friendly tone)",
    },
    "en": {
        "auto_source": "the language of the text (detect it yourself)",
        "glossary_header": "GLOSSARY:",
        "glossary_case": "Match the case of the source terms exactly.",
        "context_header": "CONTEXT:",
        "user_message": "Please translate the following text:",
        "formal_de": 'Formal "Sie" form (polite address with "Sie/Ihr/Ihnen")',
        "informal_de": 'Informal "Du" form (personal address with "du/dein/dir")',
        "formal_other": "Formal register (polite, professional tone)",
        "informal_other": "Informal register (casual, friendly tone)",
    },
}

_PRESERVE_ORIGINAL = '''CRITICAL: Preserve the EXACT tone, style, formality level, and register of the original text.
- If informal, keep it informal
- If formal, keep it formal  
- If technical jargon, preserve technical jargon
- If colloquial, keep it colloquial
- If emotional, preserve the emotional tone
- Do NOT make it more professional or academic than the original
- Translate ONLY the language, NOT the style'''

STYLE_INSTRUCTIONS: Dict[str, Dict[str, str]] = {
    "de": {
        'professionell_sie': 'Übersetze im professionellen Stil mit formeller Sie-Anrede (höflich, distanziert)',
        'professionell (sie)': 'Übersetze im professionellen Stil mit formeller Sie-Anrede (höflich, distanziert)',
        'professionell_du': 'Übersetze im professionellen Stil mit informeller Du-Anrede (freundlich, persönlich)',
        'professionell (du)': 'Übersetze im professionellen Stil mit informeller Du-Anrede (freundlich, persönlich)',
        'professionell': 'Übersetze im professionellen und formalen Stil',
        'professional': 'Translate in a professional and formal style',
        'akademisch': 'Übersetze im akademischen Stil mit entsprechender Terminologie',
        'academic': 'Translate in an academic style with appropriate terminology',
        'einfach': 'Übersetze in einfachem und verständlichem Stil',
        'einfache sprache': 'Übersetze in einfachem und verständlichem Stil',
        'simple': 'Translate in a simple and clear style',
        'technisch': 'Übersetze technisch präzise',
        'technical': 'Translate with technical precision',
        'preserve_original': _PRESERVE_ORIGINAL,
    },
    "en": {
        'professionell_sie': 'Translate in a professional style with formal address (polite, distanced)',
        'professionell (sie)': 'Translate in a professional style with formal address (polite, distanced)',
        'professionell_du': 'Translate in a professional style with informal address (friendly, personal)',
        'professionell (du)': 'Translate in a professional style with informal address (friendly, personal)',
        'professionell': 'Translate in a professional and formal style',
        'professional': 'Translate in a professional and formal style',
        'akademisch': 'Translate in an academic style with appropriate terminology',
        'academic': 'Translate in an academic style with appropriate terminology',
        'einfach': 'Translate in a simple and clear style',
        'einfache sprache': 'Translate in a simple and clear style',
        'simple': 'Translate in a simple and clear style',
        'technisch': 'Translate with technical precision',
        'technical': 'Translate with technical precision',
        'preserve_original': _PRESERVE_ORIGINAL,
    },
}


def normalize_prompt_language(value) -> str:
    return value if value in PROMPT_LANGUAGES else "de"


def language_name(code: str, prompt_language: str = "de") -> str:
    """Name of a language inside the prompt; 'auto' asks for detection."""
    prompt_language = normalize_prompt_language(prompt_language)
    if not code or code.lower() == "auto":
        return TEXTS[prompt_language]["auto_source"]
    return LANGUAGE_NAMES[prompt_language].get(code.lower(), code)
