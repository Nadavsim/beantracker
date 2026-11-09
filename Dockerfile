# The beantracker CLI as an image: docker run <image> scrape-all
#
# uv installs from the lockfile, so the image has exactly the versions CI tested. Dependencies
# are installed before the source is copied, so editing code does not reinstall them.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Dependencies first (cached until the lockfile changes)...
COPY pyproject.toml uv.lock LICENSE ./
RUN uv sync --locked --no-dev --no-install-project

# ...then the project itself. config/ and alembic.ini are read relative to the working
# directory, so they live next to the code.
COPY src ./src
COPY config ./config
COPY migrations ./migrations
COPY alembic.ini ./
RUN uv sync --locked --no-dev

# Nothing here needs root.
RUN useradd --create-home --uid 1000 bean
USER bean

ENTRYPOINT ["beantracker"]
CMD ["--help"]
