# SPDX-FileCopyrightText: 2026 Malte Dreyer
# SPDX-License-Identifier: MIT
"""
Interface texts in English and German, and the choice of interface language.

Every text shown in the interface comes from CATALOG. Both languages must
have exactly the same keys and the same {placeholders}; tests/test_i18n.py
enforces this, so the two languages cannot drift apart unnoticed.

Texts sent to the model (prompts, style instructions) are NOT part of this
catalog: the interface language must never change what the model is told.
"""

import re
from typing import Dict, Iterable, Optional

SUPPORTED_LANGUAGES = ("en", "de")
DEFAULT_LANGUAGE = "en"

CATALOG: Dict[str, Dict[str, str]] = {
    "en": {
        # --- layout -----------------------------------------------------
        "panel.original": "### 📂 Original",
        "panel.translation": "### Translation",
        "tab.file": "📄 File",
        "tab.text": "📝 Text",
        "file.label": "Formats: {formats} (max. {max_mb} MB)",
        "text.placeholder": "Enter text here...",
        "button.clear_text": "🗑️ Clear text",
        "translation.placeholder": "The translation appears here...",
        "label.download": "Download",
        "label.source_language": "From",
        "label.target_language": "To",
        "language.auto": "Detect automatically",
        "mode.direct": "🔄 Translate directly",
        "mode.styled": "🎨 Translate and adapt style",
        "glossary.title": "📚 Glossary (optional, max. {max_entries} entries)",
        "glossary.placeholder": "term{delimiter}translation (one pair per line)",
        "button.translate": "🚀 Translate",
        "button.translate_styled": "🎨 Translate with style adaptation",
        "button.cancel": "⏹ Cancel",
        # --- styles -----------------------------------------------------
        "style.professionell_sie": "Professional (formal address)",
        "style.professionell_du": "Professional (informal address)",
        "style.akademisch": "Academic",
        "style.einfach": "Plain language",
        "style.technisch": "Technical",
        "style.original": "Original style",
        # --- status -----------------------------------------------------
        "status.ready": "Ready",
        "status.enter_text": "Please enter a text.",
        "status.choose_file": "Please choose a file.",
        "status.file_too_large": "File too large: {size_mb} MB > {max_mb} MB",
        "status.pipeline_error": "❌ Processing error: {error}",
        "status.translation_failed": "❌ Translation failed",
        "status.invalid_target": "❌ Please choose a target language.",
        "status.all_failed": "❌ No section could be translated. Check the connection to the model server and the model name (details in the log).",
        "status.unreadable_file": "{name} could not be read (damaged, or not a file of this type)",
        "status.cancelled": "⚠️ Translation cancelled",
        "status.summary": "✅ {chunks} section(s) in {seconds}s ({rate}/s)",
        "status.style": "Style: {style}",
        "status.glossary_terms": "{count} glossary terms",
        "status.organization_terms": "📚 {count} from the organisation glossary",
        "status.tables": "📊 {count} tables",
        "status.code_blocks": "💻 {count} code blocks",
        "status.failed_sections": "⚠️ {failed} of {total} sections not translated (original text kept)",
        "status.failed_cells": "⚠️ {count} table cells not translated (original text kept)",
        "status.formatting_fallbacks": "⚠️ {count} paragraph(s) without character formatting (markers lost)",
        "status.inplace": "📄 Word layout preserved",
        "status.findings": "🔍 Check: {count} section(s) flagged – {details}{more}",
        "status.findings_more": " (+{count} more)",
        "status.finding_section": "Section {index}: {issues}",
        "status.average": "⌀ {seconds}s",
        "status.no_translation": "No translation available",
        "status.export_done": "✅ Export: {filename}",
        "status.export_failed": "❌ Export failed",
        "status.export_error": "❌ Export error: {error}",
        "status.unsupported_format": "Unsupported format: {format}",
        # --- progress ---------------------------------------------------
        "progress.analyzing_input": "Analyzing input...",
        "progress.processing_document": "Processing document...",
        "progress.analyzing_content": "Analyzing content...",
        "progress.starting": "Starting translation...",
        "progress.finalizing": "Finalizing...",
        "progress.done": "Translation completed",
        "progress.section": "{kind} {current}/{total}{eta}",
        "progress.kind.text": "Text",
        "progress.kind.table": "Table",
        "progress.kind.code": "Code",
        # --- quality findings (codes from the translation engine) ---------
        "finding.list_items": "{source} list items in the source, {target} in the translation",
        "finding.table_rows": "{source} table rows in the source, {target} in the translation",
        "finding.numbers": "numbers differ (missing: {missing}; added: {added})",
        "finding.length": "suspicious length: {target} characters vs. {source} in the source",
        "finding.terminology": "glossary term '{source}' -> '{target}' not applied",
        "finding.content": "content: {detail}",
        # --- Word export information block --------------------------------
        "export.heading": "Translation",
        "export.from": "From: {language}",
        "export.to": "To: {language}",
        "export.style": "Style: {style}",
        "export.created": "Created: {date}",
    },
    "de": {
        # --- layout -----------------------------------------------------
        "panel.original": "### 📂 Original",
        "panel.translation": "### Übersetzung",
        "tab.file": "📄 Datei",
        "tab.text": "📝 Text",
        "file.label": "Formate: {formats} (max. {max_mb} MB)",
        "text.placeholder": "Text hier eingeben...",
        "button.clear_text": "🗑️ Text löschen",
        "translation.placeholder": "Übersetzung erscheint hier...",
        "label.download": "Download",
        "label.source_language": "Von",
        "label.target_language": "Nach",
        "language.auto": "Automatisch erkennen",
        "mode.direct": "🔄 Direkt übersetzen",
        "mode.styled": "🎨 Übersetzen und Stil anpassen",
        "glossary.title": "📚 Glossar (optional, max. {max_entries} Einträge)",
        "glossary.placeholder": "Begriff{delimiter}Übersetzung (ein Paar pro Zeile)",
        "button.translate": "🚀 Übersetzen",
        "button.translate_styled": "🎨 Mit Stilanpassung übersetzen",
        "button.cancel": "⏹ Abbrechen",
        # --- styles -----------------------------------------------------
        "style.professionell_sie": "Professionell (Sie)",
        "style.professionell_du": "Professionell (Du)",
        "style.akademisch": "Akademisch",
        "style.einfach": "Einfache Sprache",
        "style.technisch": "Technisch",
        "style.original": "Original-Stil",
        # --- status -----------------------------------------------------
        "status.ready": "Bereit",
        "status.enter_text": "Bitte geben Sie Text ein.",
        "status.choose_file": "Bitte wählen Sie eine Datei.",
        "status.file_too_large": "Datei zu groß: {size_mb} MB > {max_mb} MB",
        "status.pipeline_error": "❌ Verarbeitungsfehler: {error}",
        "status.translation_failed": "❌ Übersetzung fehlgeschlagen",
        "status.invalid_target": "❌ Bitte eine Zielsprache wählen.",
        "status.all_failed": "❌ Kein Abschnitt konnte übersetzt werden. Bitte Verbindung zum Modellserver und Modellnamen prüfen (Details im Log).",
        "status.unreadable_file": "{name} konnte nicht gelesen werden (beschädigt oder keine Datei dieses Typs)",
        "status.cancelled": "⚠️ Übersetzung abgebrochen",
        "status.summary": "✅ {chunks} Abschnitt(e) in {seconds}s ({rate}/s)",
        "status.style": "Stil: {style}",
        "status.glossary_terms": "{count} Glossar-Begriffe",
        "status.organization_terms": "📚 {count} aus dem Glossar der Einrichtung",
        "status.tables": "📊 {count} Tabellen",
        "status.code_blocks": "💻 {count} Code-Blöcke",
        "status.failed_sections": "⚠️ {failed} von {total} Abschnitten nicht übersetzt (Originaltext beibehalten)",
        "status.failed_cells": "⚠️ {count} Tabellenzellen nicht übersetzt (Originaltext beibehalten)",
        "status.formatting_fallbacks": "⚠️ {count} Absatz/Absätze ohne Zeichenformatierung (Markierungen verloren)",
        "status.inplace": "📄 Word-Layout erhalten",
        "status.findings": "🔍 Prüfung: {count} Abschnitt(e) auffällig – {details}{more}",
        "status.findings_more": " (+{count} weitere)",
        "status.finding_section": "Abschnitt {index}: {issues}",
        "status.average": "⌀ {seconds}s",
        "status.no_translation": "Keine Übersetzung vorhanden",
        "status.export_done": "✅ Export: {filename}",
        "status.export_failed": "❌ Export fehlgeschlagen",
        "status.export_error": "❌ Exportfehler: {error}",
        "status.unsupported_format": "Nicht unterstütztes Format: {format}",
        # --- progress ---------------------------------------------------
        "progress.analyzing_input": "Eingabe wird analysiert...",
        "progress.processing_document": "Dokument wird verarbeitet...",
        "progress.analyzing_content": "Inhalt wird analysiert...",
        "progress.starting": "Übersetzung wird gestartet...",
        "progress.finalizing": "Abschluss...",
        "progress.done": "Übersetzung abgeschlossen",
        "progress.section": "{kind} {current}/{total}{eta}",
        "progress.kind.text": "Text",
        "progress.kind.table": "Tabelle",
        "progress.kind.code": "Code",
        # --- quality findings (codes from the translation engine) ---------
        "finding.list_items": "{source} Listenpunkte im Original, {target} in der Übersetzung",
        "finding.table_rows": "{source} Tabellenzeilen im Original, {target} in der Übersetzung",
        "finding.numbers": "Zahlen weichen ab (fehlen: {missing}; zusätzlich: {added})",
        "finding.length": "Länge auffällig: {target} Zeichen gegenüber {source} im Original",
        "finding.terminology": "Glossarbegriff '{source}' -> '{target}' nicht übernommen",
        "finding.content": "Inhalt: {detail}",
        # --- Word export information block --------------------------------
        "export.heading": "Übersetzung",
        "export.from": "Von: {language}",
        "export.to": "Nach: {language}",
        "export.style": "Stil: {style}",
        "export.created": "Erstellt: {date}",
    },
}

_PLACEHOLDER_RE = re.compile(r'\{(\w+)\}')


def placeholders(text: str) -> set:
    """Names of the {placeholders} in a catalog text."""
    return set(_PLACEHOLDER_RE.findall(text))


def normalize_language(code: Optional[str]) -> Optional[str]:
    """'de-AT' -> 'de'; None if the language is not supported."""
    if not code:
        return None
    primary = code.strip().lower().replace('_', '-').split('-')[0]
    return primary if primary in SUPPORTED_LANGUAGES else None


def parse_accept_language(header: Optional[str]) -> Iterable[str]:
    """Language tags of an Accept-Language header, best first."""
    if not header:
        return []
    weighted = []
    for position, part in enumerate(header.split(',')):
        pieces = part.strip().split(';')
        tag = pieces[0].strip()
        if not tag or tag == '*':
            continue
        quality = 1.0
        for param in pieces[1:]:
            param = param.strip()
            if param.startswith('q='):
                try:
                    quality = float(param[2:])
                except ValueError:
                    quality = 0.0
        if quality > 0:
            weighted.append((-quality, position, tag))
    return [tag for _, _, tag in sorted(weighted)]


def resolve_language(configured: Optional[str] = "auto",
                     accept_language: Optional[str] = None,
                     url_parameter: Optional[str] = None,
                     fallback: Optional[str] = DEFAULT_LANGUAGE) -> str:
    """
    Interface language for one page view.

    Order: ?lang= URL parameter, a fixed configured language, the browser's
    Accept-Language header (when configured is "auto"), the fallback.
    """
    chosen = normalize_language(url_parameter)
    if chosen:
        return chosen
    if configured and configured != "auto":
        chosen = normalize_language(configured)
        if chosen:
            return chosen
    else:
        for tag in parse_accept_language(accept_language):
            chosen = normalize_language(tag)
            if chosen:
                return chosen
    return normalize_language(fallback) or DEFAULT_LANGUAGE


def t(lang: Optional[str], key: str, **params) -> str:
    """Interface text for key in lang (falls back to English, then to the key)."""
    lang = normalize_language(lang) or DEFAULT_LANGUAGE
    text = CATALOG[lang].get(key) or CATALOG[DEFAULT_LANGUAGE].get(key) or key
    return text.format(**params) if params else text


def localized(value, lang: Optional[str]) -> str:
    """A configured text: either one string, or a mapping language -> string."""
    if isinstance(value, dict):
        lang = normalize_language(lang) or DEFAULT_LANGUAGE
        return value.get(lang) or value.get(DEFAULT_LANGUAGE) or next(iter(value.values()), "")
    return value or ""
