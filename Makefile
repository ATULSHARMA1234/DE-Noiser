.PHONY: install clean test analyze

# Default target: Install the project
install:
	@echo "🚀 Starting minimal installation..."
	python3 -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install .
	@if [ ! -f .env ]; then cp .env.example .env; fi
	@echo "✅ Installation complete!"
	@echo "👉 Next steps:"
	@echo "   1. Edit the '.env' file to add your API key."
	@echo "   2. Run '. .venv/bin/activate' to start using 'semantic-log'."

# Run analysis on sample data
demo:
	@.venv/bin/semantic-log analyze data/demo_incident.log --intelligence

# Run tests
test:
	@.venv/bin/pytest

# Clean up environment
clean:
	rm -rf .venv
	rm -rf *.egg-info
	find . -type d -name "__pycache__" -exec rm -rf {} +
