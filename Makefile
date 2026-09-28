.PHONY: setup check test lint app replay
setup:
	python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]" && bash scripts/setup_git.sh
lint:
	ruff check . && ruff format --check .
test:
	pytest
check: lint test
app:
	streamlit run src/aurumspread/app/main.py
replay:
	python -m aurumspread.backtest.run --config config/backtest.yaml
