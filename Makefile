.DEFAULT_GOAL := help
RECIPE ?=

help: ## Show available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n",$$1,$$2}'

doctor: ## Check Ollama and IceGate prerequisites
	@bash scripts/doctor.sh

verify: doctor ## Run a recipe and assert its telemetry. Usage: make verify RECIPE=python/openai-ollama
	@test -n "$(RECIPE)" || { echo "usage: make verify RECIPE=python/openai-ollama"; exit 2; }
	@test -d "$(RECIPE)" || { echo "no such recipe: $(RECIPE)"; exit 2; }
	@bash scripts/run_and_verify.sh "$(RECIPE)"

.PHONY: help doctor verify
