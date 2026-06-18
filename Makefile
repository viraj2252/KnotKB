.DEFAULT_GOAL := help
COMPOSE := docker compose

help:    ## list targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-12s %s\n",$$1,$$2}'

net:     ## ensure the shared external network exists
	docker network inspect hermes-net >/dev/null 2>&1 || docker network create hermes-net

up: net  ## build & start the KB stack
	$(COMPOSE) up -d --build

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
