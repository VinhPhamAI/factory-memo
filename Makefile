.PHONY: setup demo-reset

# Extra options: make setup SETUP_ARGS='--skip-seed'
setup:
	bash deployments/setup.sh $(SETUP_ARGS)

# Hide the tickets and chats left by the scripted demo (docs/development/demo-incident-flow.md).
demo-reset:
	docker compose --env-file .env -f deployments/docker-compose.yml exec api python -m backend.scripts.demo_reset
