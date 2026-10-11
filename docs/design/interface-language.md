# Design note: interface language

## Goal

Offer the interface in English and German, chosen from the browser settings,
without letting the two languages drift apart and without behaviour that
depends on displayed text.

## Text catalog

All interface texts are in one catalog, `src/i18n.py`, with one dictionary
per language. A test (`tests/test_i18n_and_context.py`) requires both
dictionaries to have exactly the same keys and the same `{placeholders}`, and
every key used in the code to exist. A text added in one language only, or a
placeholder renamed in one language only, fails the test suite instead of
showing up as a broken status line.

## Values, not labels

Drop-down lists pass identifiers, not displayed text: language codes (`de`),
style keys (`professionell_du`), mode values (`direct`). The displayed labels
come from the catalog or from `native_name` in `languages.yaml`. Behaviour
never depends on a label; for example, formal or informal address follows the
style key, not a word in the label. Languages are shown under their own name
("Deutsch", "Français") so the list needs no translation.

## Choosing the language

The language is determined for every page view, in this order:

1. URL parameter `?lang=de` / `?lang=en`
2. `interface.language`, if set to a fixed language
3. the browser's `Accept-Language` header (with `interface.language: auto`)
4. `interface.fallback_language`

The page is built in the fallback language and switched to the chosen
language when it loads; the choice is stored in the session, so status texts,
progress messages and the optional Word export block use the same language.
One server-side mechanism is used for everything. Gradio's own browser-side
translation could cover the static labels, but not status texts with numbers
in them; two mechanisms could choose different languages for the same page.

## Separate from the model

Texts sent to the model are not in the catalog (see
[translation pipeline](translation-pipeline.md)).

## Limitations

- Only English and German. Another language needs a complete dictionary in
  the catalog and an entry in `SUPPORTED_LANGUAGES`.
- The browser tab title is set when the interface is built and stays in the
  fallback (or fixed) language.
- For a moment after loading, the page shows the fallback language.
- Configured texts (`title`, `description`, `footer_links`) are
  translated only if given per language; footer links are one list for all.
