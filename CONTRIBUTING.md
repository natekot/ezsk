# Contributing to ezsk

Thanks for your interest in contributing to ezsk!

## Development setup

```bash
# Clone the repo
git clone https://github.com/YOUR_USERNAME/ezsk.git
cd ezsk

# Create a virtual environment and install dev dependencies
uv venv
uv pip install -e ".[test,web,verify,ai]"

# Run the test suite
uv run pytest tests/ -v
```

## Running locally

You need a running [slskd](https://github.com/slskd/slskd) instance. The quickest way:

```bash
# Initialize a config file
uv run ezsk --init

# Start with the web UI
uv run ezsk --serve
```

## Testing

```bash
# Run all tests
uv run pytest tests/ -v

# Run a specific test file
uv run pytest tests/test_config.py -v
```

Tests use `tmp_path` for temporary files and mock all network calls. Never hit real APIs in tests.

## Code style

- Python 3.11+ with modern type hints (`list[str]`, `X | None`)
- No linter is enforced yet -- just keep it consistent with the existing code
- Prefer simple, readable code over clever abstractions

## Pull requests

1. Fork the repo and create a branch from `main`
2. Make your changes
3. Add or update tests as needed
4. Ensure `uv run pytest tests/ -v` passes
5. Open a PR with a clear description of the change

## Reporting issues

Open an issue on GitHub. Include:
- What you expected to happen
- What actually happened
- Steps to reproduce
- Your OS and Python version
