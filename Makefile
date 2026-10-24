.PHONY: setup

# Extra options: make setup SETUP_ARGS='--skip-seed'
setup:
	bash deployments/setup.sh $(SETUP_ARGS)
