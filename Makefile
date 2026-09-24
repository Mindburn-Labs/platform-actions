.PHONY: check lint test

# CI v2 contract: `make check` runs every real gate in this repository.
check: lint test

# actionlint also runs shellcheck on every `run:` block, which is where this
# repository's shell lives, so shellcheck must be present rather than skipped.
lint:
	@command -v actionlint >/dev/null || { echo "actionlint is required" >&2; exit 1; }
	@command -v shellcheck >/dev/null || { echo "shellcheck is required" >&2; exit 1; }
	actionlint .github/workflows/*.yml templates/ci.yml
	git ls-files -z '*.sh' | xargs -0 -r shellcheck

test:
	python3 -m unittest discover -s tests -p 'test_*.py'
