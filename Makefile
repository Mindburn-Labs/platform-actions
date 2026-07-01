.PHONY: setup test lint build agent-context

setup:
	@echo "Setting up local environment for dev..."

test:
	@python3 -m unittest discover -s tests -p 'test_*.py'

lint:
	@echo "Executing lint checks..."

build:
	@echo "Compiling and packaging build artifacts..."

agent-context:
	@echo "Generating contextual index for agent run..."
