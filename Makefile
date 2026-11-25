# valetkey developer tasks. Nothing here touches a secret.
VERSION := $(shell cat VERSION)
PREFIX  ?= $(HOME)/.local/bin
SHELL_SCRIPTS := install.sh client/install-version-tools broker/run-tests broker/run-security-tests $(filter-out broker/bin/broker-approval,$(wildcard broker/*.sh broker/bin/broker-* broker/bin/with-bw-session deploy/*/*.sh \
  bellhop/*.sh bellhop/testvm/*.sh bellhop/fakes/tailscale-drill))

.PHONY: help test test-security lint install-client dist clean

help:
	@echo "make test             run the unit and policy tests (stdlib only, fakes for bw/tailscale/fnox)"
	@echo "make lint             bash -n on every script, shellcheck if installed, Python compile check, no real names"
	@echo "make install-client   ./install.sh client --prefix \$$PREFIX [BROKER_URL=https://broker.<tailnet>.ts.net]"
	@echo "make dist             dist/valetkey-$(VERSION).tar.gz from HEAD (git archive)"

test:
	broker/run-tests
	python3 -m unittest bellhop/test_bellhop.py

test-security:
	bash broker/run-security-tests

lint:
	@for f in $(SHELL_SCRIPTS); do bash -n $$f || exit 1; done; echo "ok    bash -n"
	@if command -v shellcheck >/dev/null; then shellcheck -S warning $(SHELL_SCRIPTS) && echo "ok    shellcheck"; \
	 else echo "skip  shellcheck (not installed)"; fi
	@python3 -m py_compile broker/bin/broker-approval broker/*.py broker/brokerlib/*.py broker/tools/*.py agents/*.py bellhop/*.py bellhop/bellhop bellhop/fakes/fakemcp.py \
	  && echo "ok    py_compile"
	@# This repository is public: examples use example/test names, never a real tailnet or home directory.
	@if git grep -nIoE '[a-z0-9-]+\.ts\.net|/(home|Users)/[a-z0-9_-]+' -- . ':!Makefile' \
	    | grep -vE ':(example|test|other|evil)\.ts\.net$$'; then \
	  echo "FAIL  real tailnet name or home path above; use broker.example.ts.net and ~"; exit 1; \
	 else echo "ok    no real tailnet names or home paths"; fi

install-client:
	./install.sh client --prefix $(PREFIX) $(if $(BROKER_URL),--broker-url $(BROKER_URL))

dist:
	@mkdir -p dist
	git archive --format=tar.gz --prefix=valetkey-$(VERSION)/ -o dist/valetkey-$(VERSION).tar.gz HEAD
	@echo "dist/valetkey-$(VERSION).tar.gz"

clean:
	rm -rf dist
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
