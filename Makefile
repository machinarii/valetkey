# valetkey developer tasks. Nothing here touches a secret.
VERSION := $(shell cat VERSION)
PREFIX  ?= $(HOME)/.local/bin
SHELL_SCRIPTS := install.sh relay/run-tests relay/run-security-tests $(wildcard broker/*.sh broker/bin/* deploy/*/*.sh)

.PHONY: help test test-security lint install-client dist clean

help:
	@echo "make test             run the unit and policy tests (stdlib only, fakes for bw/tailscale/fnox)"
	@echo "make lint             bash -n on every script, shellcheck if installed, Python compile check, no real names"
	@echo "make install-client   ./install.sh client --prefix \$$PREFIX [RELAY_URL=https://broker.<tailnet>.ts.net]"
	@echo "make dist             dist/valetkey-$(VERSION).tar.gz from HEAD (git archive)"

test:
	relay/run-tests

test-security:
	bash relay/run-security-tests

lint:
	@for f in $(SHELL_SCRIPTS); do bash -n $$f || exit 1; done; echo "ok    bash -n"
	@if command -v shellcheck >/dev/null; then shellcheck -S warning $(SHELL_SCRIPTS) && echo "ok    shellcheck"; \
	 else echo "skip  shellcheck (not installed)"; fi
	@python3 -m py_compile relay/*.py relay/relaylib/*.py relay/tools/*.py && echo "ok    py_compile"
	@# This repository is public: examples use example/test names, never a real tailnet or home directory.
	@if git grep -nIoE '[a-z0-9-]+\.ts\.net|/(home|Users)/[a-z0-9_-]+' -- . ':!Makefile' \
	    | grep -vE ':(example|test|other|evil)\.ts\.net$$'; then \
	  echo "FAIL  real tailnet name or home path above; use broker.example.ts.net and ~"; exit 1; \
	 else echo "ok    no real tailnet names or home paths"; fi

install-client:
	./install.sh client --prefix $(PREFIX) $(if $(RELAY_URL),--relay-url $(RELAY_URL))

dist:
	@mkdir -p dist
	git archive --format=tar.gz --prefix=valetkey-$(VERSION)/ -o dist/valetkey-$(VERSION).tar.gz HEAD
	@echo "dist/valetkey-$(VERSION).tar.gz"

clean:
	rm -rf dist
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
