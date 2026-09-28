.PHONY: setup check test lint app replay fetch ingest
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
fetch:            # usage: make fetch START=2025-01-01 END=2026-09-26 (needs data_source.yaml url)
	python -m aurumspread.data.cli fetch --start $(START) --end $(END)
ingest:           # raw CSVs -> data/processed/*.parquet + audit summary
	python -m aurumspread.data.cli ingest
