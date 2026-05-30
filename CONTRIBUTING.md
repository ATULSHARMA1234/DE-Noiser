# Contributing to SemanticOS

First off, thank you for considering contributing to SemanticOS! It's people like you that make SemanticOS such a great tool.

## How Can I Contribute?

### Reporting Bugs
This section guides you through submitting a bug report. Following these guidelines helps maintainers and the community understand your report, reproduce the behavior, and find related reports.
* Use a clear and descriptive title.
* Describe the exact steps to reproduce the problem.
* Provide specific examples to demonstrate the steps.

### Suggesting Enhancements
* Use a clear and descriptive title.
* Provide a step-by-step description of the suggested enhancement.
* Provide specific examples to demonstrate the steps.
* Describe the current behavior and explain which behavior you expected to see instead.

### Pull Requests
* Fill in the required template.
* Do not include issue numbers in the PR title.
* Follow the Python and TypeScript style guides.
* Ensure all tests pass (`pytest` and `npm run test`).

## Development Setup

1. Clone the repository
2. Run `docker-compose up -d` to start the backend services (Redpanda, ClickHouse, Redis).
3. Start the FastAPI backend: `cd src && uv run python -m uvicorn denoiser.api.main:app`
4. Start the Next.js frontend: `cd web && npm install && npm run dev`

## Code of Conduct
By participating in this project, you are expected to uphold our Code of Conduct. Please be respectful to all community members.
