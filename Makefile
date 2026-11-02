PY ?= python3

.PHONY: setup replay dev test build

setup:
	$(PY) scripts/rw.py setup

replay:
	$(PY) scripts/rw.py replay

dev:
	$(PY) scripts/rw.py dev

test:
	$(PY) scripts/rw.py test

build:
	$(PY) scripts/rw.py build
