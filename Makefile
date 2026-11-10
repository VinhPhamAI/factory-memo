.PHONY: setup demo-reset demo-accounts

# Extra options: make setup SETUP_ARGS='--skip-seed'
setup:
	bash deployments/setup.sh $(SETUP_ARGS)

# Demo helpers run in a one-off API container on the current backend source,
# so they need no image rebuild. See docs/development/demo-incident-flow.md.
DEMO_RUN = docker compose --env-file .env -f deployments/docker-compose.yml run --rm --no-deps \
	-v "$(CURDIR)/backend:/app/backend:ro" api python -m

# Hide the tickets and chats left by the scripted demo.
demo-reset:
	$(DEMO_RUN) backend.scripts.demo_reset

# Create the demo employee, admin and expert; credentials go to data/demo/accounts.json.
demo-accounts:
	$(DEMO_RUN) backend.scripts.demo_accounts
