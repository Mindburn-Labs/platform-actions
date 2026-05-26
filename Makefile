.PHONY: setup test lint build agent-context

setup:
	@echo "Setting up local environment for dev..."

test:
	@echo "Running local test suite..."

lint:
	@echo "Executing lint checks..."

build:
	@echo "Compiling and packaging build artifacts..."

agent-context:
	@echo "Generating contextual index for agent run..."
