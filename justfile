# Anolis Workbench dev commands.
# Recipes wrap the per-language commands documented in CONTRIBUTING.md
# (Python via uv, frontend via npm) plus the Rust/Tauri wrapper in desktop/.

# List available recipes
default:
    @just --list

# Install dependencies (Python + frontend)
setup:
    uv sync --locked --extra dev
    cd frontend && npm ci

# Format code (Python + frontend + Rust)
fmt:
    uv run ruff format .
    cd frontend && npm run format
    cd desktop/src-tauri && cargo fmt

# Lint (Python + frontend + Rust)
lint:
    uv run ruff check . --output-format=github
    cd frontend && npm run lint
    cd desktop/src-tauri && cargo clippy -- -D warnings

# CI-equivalent checks: format-check + lint + typecheck (Python + frontend + Rust)
check:
    uv run ruff check . --output-format=github
    uv run ruff format --check .
    uv run mypy anolis_workbench tests
    cd frontend && npm run format:check
    cd frontend && npm run lint
    cd frontend && npm run check
    cd desktop/src-tauri && cargo fmt --check
    cd desktop/src-tauri && cargo clippy -- -D warnings
    cd desktop/src-tauri && cargo check

# Run tests + contract validators (Python + frontend)
test:
    uv run pytest tests/ -v --tb=short
    uv run python contracts/validate-composer-control-openapi.py
    uv run python contracts/validate-workbench-api-openapi.py
    cd frontend && npm run test:unit:coverage
    cd frontend && npm run test:components:coverage

# Build the frontend bundle
build:
    cd frontend && npm run build
