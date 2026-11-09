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
uv run beantracker scrape <slug> --no-store   # live check, prints a table, writes nothing (--cache: reuse pages for 6 h)
uv run beantracker scrape <slug>              # stores a run (--strict, --reset-baseline); scrape-all; runs list
uv run beantracker catalog --origin Ethiopia --in-stock   # search the stored catalog, cheapest per 100 g first
```
Windows note: Python is installed to `C:\Users\USER\uvpython` (env var `UV_PYTHON_INSTALL_DIR`) because uv's default location failed with a junction error.

## Roasters
Shopify: Nomena (ILS, Hebrew, grams=0), September (USD), Hydrangea (oz; variant grams disagree with titles). Custom HTML: DAK (WooCommerce, EUR; `adapters/dak.py`: listing cards give everything but weight, which costs one detail-page request per coffee; only the default size is priced - other sizes need the WooCommerce Store API). Extras for config-only demos: La Cabra, The Barn.

## Gotchas
- selectolax 1.0 dropped the Modest backend: import from `selectolax.lexbor`.
- Shopify `grams` is unreliable (0, or copied across variants): size comes from the variant title first. Grind variants with identical size+price collapse into one Bean.
- Fixtures in `tests/fixtures/` are real captures; refresh them deliberately, never in tests.
- Runs: the run row is committed before fetching; fetch AND persistence errors become `failed` runs. Ctrl+C/cancellation is recorded as `Interrupted: ...` and re-raised; a run still `running` after 2 h (killed process) is closed as `Stale` by the roaster's next run.
- Health baseline = median product count of the last `baseline_runs` (5) ok runs, so a slow decline is caught. Warning runs never join the baseline, so a real, permanent catalog change needs `scrape <slug> --reset-baseline` (flag `baseline_reset` marks where history restarts; stored in `health_flags`, no migration). The first ok run is flagged `baseline` (everything is 'new').
- CLI exit codes: 0 ok (warnings too, unless `--strict`), 1 a run failed, 2 bad usage/config (unknown slug, missing or invalid config file, unknown adapter). `scrape-all` validates the config up front and isolates roasters: one unexpected error does not stop the rest.
- Live findings (Oct 2026): September/Hydrangea keep their whole sold-out back catalog (~90% out of stock) and sell gear/gift cards in the same feed, so per-roaster `exclude_*` options matter. ~45% of September titles name no country (None, not guessed). Nomena lists each coffee twice (product types `coffee` and `קפה`); not deduplicated yet. DAK `Steeped` has no weight.
- Tests that need Postgres use a throwaway `beantracker_test` database (`tests/conftest.py`).
- HTTP: `http/robots.py` implements RFC 9309 (wildcards, `$`, longest match, named groups); the stdlib parser ignores wildcards. robots.txt that cannot be read (5xx, 429, 401/403, network error) means the site is off limits; a missing file or other 4xx means no restrictions. Redirects are followed by hand so every hop is robots-checked and throttled per host. Network errors and 429/5xx are retried (Retry-After as seconds or date, capped at 60 s, longer waits give up).
- Cache: pages expire after `cache_ttl_hours` (6). A stored run never reads the cache and `--cache` needs `--no-store`, so cached pages cannot become fresh-looking snapshots.
- Health also watches descriptive-field coverage (origin, process, roast, varietal, notes): a field normally filled for >= 20% of sizes that falls by > 30 points vs the median of recent ok runs warns as `coverage_drop:<field>`. Coverage is stored per run in `scrape_run.field_coverage`.
- Catalog: `repo.current_beans()` = latest snapshot per size, restricted to coffees seen in the roaster's latest *ok* run (so delisted ones disappear, and a coffee first seen in a warning run appears after the next ok run). `repo.delisted_coffees()` reports what left between two ok runs; `scrape` prints the count.
- `coffee.raw` is slimmed: Shopify keeps only what `_parse` reads (plus timestamps), so a stored payload re-parses to the same product; DAK stores everything its adapter read.
