STACKS = dns landing netbird

.PHONY: fmt lint check sync

fmt:
	@for s in $(STACKS); do [ -f "$$s/pyproject.toml" ] && uv run --directory $$s ruff format . || true; done

lint:
	@for s in $(STACKS); do [ -f "$$s/pyproject.toml" ] && uv run --directory $$s ruff check . || true; done

check:
	@for s in $(STACKS); do \
		[ -f "$$s/pyproject.toml" ] || continue; \
		uv run --directory $$s ruff format --check . || exit 1; \
		uv run --directory $$s ruff check . || exit 1; \
	done

sync:
	@for s in $(STACKS); do [ -f "$$s/pyproject.toml" ] && uv sync --project $$s --dev || true; done
