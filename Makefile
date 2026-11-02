.PHONY: build install update assets test run
PYTHON ?= .venv/bin/python

build:
	./install.sh --build
install:
	./install.sh install
update:
	./install.sh update
assets:
	$(PYTHON) tools/assets.py
test:
	$(PYTHON) -m unittest discover -s tests -v
run:
	$(PYTHON) tests/vm.py fixture
	$(PYTHON) tests/vm.py run
