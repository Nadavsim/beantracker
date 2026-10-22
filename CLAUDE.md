# beantracker

Scrapes specialty coffee roasters, normalizes products into a catalog, keeps price/availability history as per-run snapshots. Phase 1 = scrape + store + CLI + health checks. Out of scope until later: web UI, alerts, LLM extraction, deployment.

## Architecture (data flow)
`RoasterAdapter.fetch() -> list[RawProduct]` -> `normalize` (pure) -> `Bean` rows -> repo writes `coffee` / `offer` / `offer_snapshot` under a `scrape_run` -> `health` evaluates the run.

- **Adapters** only fetch and parse into `RawProduct`; they never normalize. One generic `ShopifyAdapter` (config-driven via `config/roasters.yaml`) plus custom HTML adapters (selectolax). Registered by name in `adapters/registry.py`.
- **Normalization** is pure functions (weights, prices, origin, process, roast); unknown -> NULL, never guessed.
- **Data model is snapshot-based**: `coffee` is the stable identity (new lot = first_seen_run_id == this run), `offer` is a size, `offer_snapshot` is append-only per run. History is a query, not logic.
- **Money**: integer minor units + ISO currency. Shopify `/products.json` has no currency, so currency is required per-roaster config.
- **Polite scraping**: descriptive User-Agent, robots.txt respected, per-domain rate limit, retry/backoff honoring Retry-After, dev-only disk cache. No login-walled content.
- **Health checks** per run: zero products, big count drop vs previous ok run, high missing-field rate. Status stored on `scrape_run`.

## Conventions
- Python 3.12, uv, `src/` layout, strict mypy, ruff (line length 100).
- Adapter tests use saved fixtures in `tests/fixtures/<roaster>/`, never live sites. Repository tests run against real Postgres (marker `integration`), not SQLite.
- Comments only for non-obvious "why". One logical change per commit.

## Commands
```
uv sync                         # install
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest                   # add -m "not integration" to skip DB tests
uv run beantracker --help
docker compose up -d db && uv run beantracker db upgrade
uv run beantracker scrape <slug> --no-store   # live check, prints a table, writes nothing
uv run beantracker scrape <slug> [--cache]    # stores a run; scrape-all; runs list
```
Windows note: Python is installed to `C:\Users\USER\uvpython` (env var `UV_PYTHON_INSTALL_DIR`) because uv's default location failed with a junction error.

## Roasters
Shopify: Nomena (ILS, Hebrew, grams=0), September (USD), Hydrangea (oz; variant grams disagree with titles). Custom HTML: DAK (WooCommerce, EUR; `adapters/dak.py`: listing cards give everything but weight, which costs one detail-page request per coffee; only the default size is priced - other sizes need the WooCommerce Store API). Extras for config-only demos: La Cabra, The Barn.

## Gotchas
- selectolax 1.0 dropped the Modest backend: import from `selectolax.lexbor`.
- Shopify `grams` is unreliable (0, or copied across variants): size comes from the variant title first. Grind variants with identical size+price collapse into one Bean.
- Fixtures in `tests/fixtures/` are real captures; refresh them deliberately, never in tests.
- Runs: the run row is committed before fetching; fetch AND persistence errors become `failed` runs (never left `running`). Warning runs don't become the count-drop baseline; the first ok run is flagged `baseline` (everything is 'new').
- Live findings (Oct 2026): September/Hydrangea keep their whole sold-out back catalog (~90% out of stock) and sell gear/gift cards in the same feed, so per-roaster `exclude_*` options matter. ~45% of September titles name no country (None, not guessed). Nomena lists each coffee twice (product types `coffee` and `קפה`); not deduplicated yet. DAK `Steeped` has no weight.
- Tests that need Postgres use a throwaway `beantracker_test` database (`tests/conftest.py`).
