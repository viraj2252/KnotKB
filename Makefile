.DEFAULT_GOAL := help
COMPOSE := docker compose
HERMES_FLAGS := -f docker-compose.yml -f docker-compose.hermes.yml

help:    ## list targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-18s %s\n",$$1,$$2}'

init:    ## pre-create the vault dir as the current user (avoids root-owned dirs on Linux)
	mkdir -p "$${KB_HOST_PATH:-./kb-data}"

seed-example: init ## copy the example vault structure into an empty vault dir
	cp -Rn example/wiki example/decisions example/sources example/memory "$${KB_HOST_PATH:-./kb-data}/" 2>/dev/null || true

up: init ## build & start the standalone KB stack
	$(COMPOSE) up -d --build

net:     ## ensure the shared hermes-net network exists (Hermes integration only)
	docker network inspect hermes-net >/dev/null 2>&1 || docker network create hermes-net

up-hermes: init net ## build & start the stack joined to hermes-net (for Hermes + claude-proxy)
	$(COMPOSE) $(HERMES_FLAGS) up -d --build

down:    ## stop the KB stack (pgdata volume survives)
	$(COMPOSE) down

logs:    ## tail kb-mcp logs
	$(COMPOSE) logs -f kb-mcp

health:  ## probe the MCP health endpoint
	curl -fsS http://127.0.0.1:$${KB_MCP_PORT:-8077}/health && echo

reindex: ## rebuild the pgvector index from markdown
	$(COMPOSE) exec kb-mcp kb reindex

lint:    ## run KB health checks (tag drift, index health)
	$(COMPOSE) exec kb-mcp kb lint

verify:  ## end-to-end verification on an isolated stack (port 8078, throwaway data)
	./scripts/verify-standalone.sh

schedule-install:   ## install the nightly consolidate job (launchd/systemd/cron autodetect)
	./deploy/install-scheduler.sh install

schedule-uninstall: ## remove the nightly consolidate job
	./deploy/install-scheduler.sh uninstall

schedule-status:    ## show nightly job status
	./deploy/install-scheduler.sh status
