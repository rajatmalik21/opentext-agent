.RECIPEPREFIX := >
export PYTHONPATH := src
.PHONY: test typecheck lint eval ingest serve scan docker tf-check all
all: test typecheck scan
test:
> python -m pytest -q
typecheck:
> python -m mypy src
lint:
> python -m ruff check src tests tools
eval:
> python -m ota.evals retrieval && python -m ota.evals matrix
ingest:
> python -m ota.cli ingest
serve: ingest
> python -m ota.cli serve --port 8080
scan:
> python tools/scan_secrets.py
docker:
> docker build -t ota-agent:local .
tf-check:
> cd infra/terraform && terraform fmt -check -recursive && terraform init -backend=false && terraform validate
