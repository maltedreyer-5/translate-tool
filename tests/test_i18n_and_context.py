# SPDX-FileCopyrightText: 2026 Malte Dreyer
# SPDX-License-Identifier: MIT
"""Interface languages, style continuity between sections, request limits."""
import asyncio
import logging

import pytest

from src.config import load_config
from src.i18n import (CATALOG, SUPPORTED_LANGUAGES, placeholders, resolve_language,
                      parse_accept_language, t, localized)
from src.llm_client import LLMClient, CompletionResponse
from src.chunking.simple_chunker import SimpleChunker
from src.translation.translation_engine import TableAwareTranslationEngine

logging.disable(logging.CRITICAL)


# --------------------------------------------------------------------------
# catalog
# --------------------------------------------------------------------------
def test_catalog_languages_have_the_same_keys():
    reference = set(CATALOG["en"])
    for lang in SUPPORTED_LANGUAGES:
        assert set(CATALOG[lang]) == reference, f"keys differ in '{lang}'"


def test_catalog_placeholders_match():
    for key, text in CATALOG["en"].items():
        for lang in SUPPORTED_LANGUAGES:
            assert placeholders(CATALOG[lang][key]) == placeholders(text), f"{lang}:{key}"


def test_every_used_key_exists():
    """Keys used in the code must exist in the catalog."""
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "src"
    used = set()
    for path in root.rglob("*.py"):
        used |= set(re.findall(r'\bt\([^,()]+,\s*f?"([a-z_]+\.[a-z_.]+)"', path.read_text()))
    static = {k for k in used if "{" not in k}
    missing = static - set(CATALOG["en"])
    assert not missing, missing


@pytest.mark.parametrize("configured,header,url,expected", [
    ("auto", "de-DE,de;q=0.9,en;q=0.8", None, "de"),
    ("auto", "fr-FR,fr;q=0.9,en;q=0.5", None, "en"),
    ("auto", "fr-FR", None, "en"),            # fallback
    ("auto", "en;q=0.3,de;q=0.7", None, "de"),  # quality values
    ("de", "en-US", None, "de"),              # fixed language wins over browser
    ("auto", "en-US", "de", "de"),            # URL parameter wins
    ("en", "de", "xx", "en"),                 # unknown URL parameter ignored
    ("auto", None, None, "en"),
])
def test_language_resolution(configured, header, url, expected):
    assert resolve_language(configured, header, url, "en") == expected


def test_fallback_language_is_configurable():
    assert resolve_language("auto", "fr", None, "de") == "de"


def test_accept_language_order():
    assert parse_accept_language("en;q=0.5, de, fr;q=0") == ["de", "en"]


def test_t_and_localized():
    assert t("de", "status.ready") == "Bereit"
    assert t("fr", "status.ready") == "Ready"
    assert t("en", "status.tables", count=3) == "📊 3 tables"
    assert localized({"en": "A", "de": "B"}, "de") == "B"
    assert localized("Same", "de") == "Same"


def test_style_values_are_keys_and_labels_are_localized():
    from src.app import style_label, style_key_for_label, formality_for_style
    assert style_label("professionell_du", "en") == "Professional (informal address)"
    assert style_label("professionell_du", "de") == "Professionell (Du)"
    # the dropdown passes the key; labels of both languages are understood too
    for value in ("professionell_du", "Professional (informal address)", "Professionell (Du)"):
        assert formality_for_style(style_key_for_label(value)) == "informal"


def test_language_dropdown_values_are_codes():
    from src.app import UnifiedLanguageManager
    manager = UnifiedLanguageManager(config=load_config())
    targets = dict((code, label) for label, code in manager.target_choices("en"))
    assert targets["de"] == "Deutsch" and targets["fr"] == "Français"
    assert "auto" not in targets
    sources = dict((code, label) for label, code in manager.source_choices("de"))
    assert sources["auto"] == "Automatisch erkennen"
    assert manager.to_code("de") == "de" and manager.to_code("Deutsch") == "de"


# --------------------------------------------------------------------------
# style continuity between sections
# --------------------------------------------------------------------------
class RecordingClient(LLMClient):
    def __init__(self, config):
        super().__init__(config)
        self.order = []
        self.systems = {}

    async def complete(self, messages, temperature=None, max_tokens=None, stream=False, **kw):
        user = messages[-1].content
        text = user.split("\n\n", 1)[1] if "\n\n" in user else user
        self.order.append(text[:12])
        self.systems[text[:12]] = messages[0].content
        await asyncio.sleep(0.01)
        return CompletionResponse(content=f"TR {text}", model="stub", finish_reason="stop")


def _translate(mode):
    config = load_config()
    config.translation.auto_glossary = False
    config.translation.context_mode = mode
    paragraphs = [f"Abschnitt{i} " + ("Wort " * 60).strip() + "." for i in range(4)]
    chunks = SimpleChunker(max_tokens=60, config=config).chunk_text("\n\n".join(paragraphs), "de")
    assert len(chunks.chunks) >= 3
    client = RecordingClient(config)
    engine = TableAwareTranslationEngine(client, config)
    asyncio.run(engine.translate_document(chunks, "de", "en", "preserve_original"))
    return client


def test_two_wave_uses_first_translation_as_style_reference():
    client = _translate("two_wave")
    first = client.order[0]
    assert first.startswith("Abschnitt0")
    assert "STYLE REFERENCE" not in client.systems[first]
    for key, system in client.systems.items():
        if key != first:
            assert "STYLE REFERENCE" in system and "TR Abschnitt0" in system


def test_sequential_hands_on_previous_translation():
    client = _translate("sequential")
    assert client.order == sorted(client.order)
    assert "TR Abschnitt1" in client.systems[client.order[2]]


def test_parallel_mode_has_no_style_reference():
    client = _translate("parallel")
    assert all("STYLE REFERENCE" not in s for s in client.systems.values())


def test_cancellation_propagates_from_first_wave():
    config = load_config()
    config.translation.auto_glossary = False

    class Slow(LLMClient):
        async def complete(self, *a, **k):
            await asyncio.sleep(10)

    async def scenario():
        chunks = SimpleChunker(max_tokens=700, config=config).chunk_text("Ein Satz.", "de")
        engine = TableAwareTranslationEngine(Slow(config), config)
        job = asyncio.ensure_future(engine.translate_document(chunks, "de", "en"))
        await asyncio.sleep(0.1)
        job.cancel()
        with pytest.raises(asyncio.CancelledError):
            await job

    asyncio.run(scenario())


# --------------------------------------------------------------------------
# application-wide request limit
# --------------------------------------------------------------------------
def test_application_wide_request_limit():
    from aiohttp import web
    from src.llm_client import Message, MessageRole
    state = {"now": 0, "peak": 0}

    async def handler(request):
        state["now"] += 1
        state["peak"] = max(state["peak"], state["now"])
        await asyncio.sleep(0.05)
        state["now"] -= 1
        return web.json_response({"choices": [{"message": {"content": "ok"},
                                               "finish_reason": "stop"}], "model": "m"})

    async def scenario():
        app = web.Application()
        app.router.add_post('/v1/chat/completions', handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        config = load_config()
        config.llm.api_base = f"http://127.0.0.1:{port}/v1"
        shared = asyncio.Semaphore(2)
        clients = [LLMClient(config, shared_semaphore=shared) for _ in range(3)]
        try:
            await asyncio.gather(*(c.complete([Message(MessageRole.USER, "x")])
                                   for c in clients for _ in range(4)))
        finally:
            for c in clients:
                await c.close()
            await runner.cleanup()

    asyncio.run(scenario())
    assert state["peak"] == 2
