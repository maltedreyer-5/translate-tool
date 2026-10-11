# SPDX-FileCopyrightText: 2026 Malte Dreyer
# SPDX-License-Identifier: MIT
"""Glossary of the organisation: file format, language pairs, precedence, reload."""
import asyncio
import logging
import os
import time

import pytest

from src.organization_glossary import OrganizationGlossary

logging.disable(logging.CRITICAL)

CSV = """# comment
source_lang,target_lang,term,translation
de,en,Akademischer Senat,Academic Senate
de,en,Studierendenservice,Student Services
de,fr,Akademischer Senat,Sénat académique
en,de,Academic Senate,Akademischer Senat
de,en,,missing term
"""


@pytest.fixture
def glossary_file(tmp_path):
    path = tmp_path / "org.csv"
    path.write_text(CSV, encoding="utf-8")
    return path


def test_entries_per_language_pair(glossary_file):
    g = OrganizationGlossary(glossary_file)
    assert g.entries_for("de", "en") == {"Akademischer Senat": "Academic Senate",
                                         "Studierendenservice": "Student Services"}
    assert g.entries_for("de", "fr") == {"Akademischer Senat": "Sénat académique"}
    assert g.entries_for("en", "fr") == {}
    assert any("incomplete" in p for p in g.problems)


def test_automatic_source_uses_all_entries_for_the_target(glossary_file):
    g = OrganizationGlossary(glossary_file)
    assert set(g.entries_for("auto", "en")) == {"Akademischer Senat", "Studierendenservice"}


def test_only_terms_occurring_in_the_document(glossary_file):
    g = OrganizationGlossary(glossary_file)
    text = "Der akademische senat tagt morgen."
    assert g.relevant_entries("de", "en", text) == {"Akademischer Senat": "Academic Senate"}
    assert g.relevant_entries("de", "en", text, case_sensitive=True) == {}
    assert g.relevant_entries("de", "en", "Nichts davon.") == {}


@pytest.mark.parametrize("text", [
    "Sitzung des Akademischen Senats", "dem Akademischen Senat", "Der Akademische Senat",
])
def test_inflected_forms_are_recognised(glossary_file, text):
    g = OrganizationGlossary(glossary_file)
    assert "Akademischer Senat" in g.relevant_entries("de", "en", text)


def test_no_match_inside_other_words(tmp_path):
    path = tmp_path / "g.csv"
    path.write_text("source_lang,target_lang,term,translation\nde,en,Rat,Council\n")
    g = OrganizationGlossary(path)
    assert g.relevant_entries("de", "en", "Der Rat tagt.") == {"Rat": "Council"}
    assert g.relevant_entries("de", "en", "Das Gerät und der Ratschlag.") == {}


def test_changes_are_picked_up_without_restart(glossary_file):
    g = OrganizationGlossary(glossary_file)
    assert "Rechenzentrum" not in g.entries_for("de", "en")
    glossary_file.write_text(CSV + "de,en,Rechenzentrum,Computing Centre\n", encoding="utf-8")
    later = time.time() + 5
    os.utime(glossary_file, (later, later))
    assert g.entries_for("de", "en")["Rechenzentrum"] == "Computing Centre"


def test_missing_file_and_bad_header(tmp_path):
    assert OrganizationGlossary(tmp_path / "absent.csv").entries_for("de", "en") == {}
    bad = tmp_path / "bad.csv"
    bad.write_text("term,translation\nA,B\n")
    g = OrganizationGlossary(bad)
    assert g.entries_for("de", "en") == {} and "header" in g.problems[0]
    assert not OrganizationGlossary(None).configured


def test_semicolon_and_windows_encoding(tmp_path):
    path = tmp_path / "excel.csv"
    path.write_bytes("source_lang;target_lang;term;translation\nde;en;Prüfungsamt;Examinations Office\n"
                     .encode("cp1252"))
    assert OrganizationGlossary(path, ";").entries_for("de", "en") == {"Prüfungsamt": "Examinations Office"}


def test_app_applies_organisation_terms_with_user_precedence(glossary_file, monkeypatch):
    import src.app as app_module
    from src.llm_client import LLMClient, CompletionResponse

    prompts = []

    class Recorder(LLMClient):
        def __init__(self, config, shared_semaphore=None):
            super().__init__(config)

        async def complete(self, messages, **kw):
            prompts.append(messages[0].content)
            user = messages[-1].content
            return CompletionResponse(content=user.split("\n\n", 1)[-1], model="m", finish_reason="stop")

    monkeypatch.setattr(app_module, "LLMClient", Recorder)
    monkeypatch.setenv("TRANSLATION_GLOSSARY__ORGANIZATION_FILE", str(glossary_file))
    app = app_module.UnifiedDocumentTranslationApp()
    try:
        app.config.translation.auto_glossary = False
        text = "Der Akademische Senat und der Studierendenservice tagen."
        _, translation, status, _ = asyncio.run(app.process_and_translate_unified(
            "Text", text, None, "de", "en", "direct", "", "Studierendenservice,Student Office",
            progress=lambda *a, **k: None, lang="en"))
        system = prompts[-1]
        assert "'Akademischer Senat' → 'Academic Senate'" in system
        assert "'Studierendenservice' → 'Student Office'" in system   # user entry wins
        assert "Student Services" not in system
        assert "1 from the organisation glossary" in status
    finally:
        app.cleanup()
