SHELL := /bin/bash
PYTHON ?= python3
VENV   := .venv
VPY    := $(VENV)/bin/python
SRC    := docker compose --env-file .env -f docker/cloudera/docker-compose.yml
TGT    := docker compose --env-file .env -f docker/trino/docker-compose.yml

.PHONY: help env venv data data-local data-docker source-up source-up-impala hue-up hue-down source-load source-down target-up target-init target-down up down clean beeline trino nim-up nim-down agent-build agent-probe agent-run agent-run-offline agent-run-local agent-ask agent-test

help:            ## Lista de comandos
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-18s %s\n", $$1, $$2}'

env:             ## Crea .env desde .env.example (si no existe)
	@test -f .env || cp .env.example .env

venv:            ## Entorno Python para el generador (requiere python3-venv)
	@$(VPY) -m pip --version >/dev/null 2>&1 || { rm -rf $(VENV); $(PYTHON) -m venv $(VENV); }
	$(VPY) -m pip install -q -r data_generator/requirements.txt

GEN_ARGS = --out data/source --scale $${SCALE:-1.0} --seed $${SEED:-42}

data:            ## Genera el dataset sintetico (SCALE=1.0 SEED=42); usa Docker si no hay python3-venv
	@if $(MAKE) --no-print-directory venv >/dev/null 2>&1; then \
		$(MAKE) --no-print-directory data-local; \
	else \
		echo ">>> No se pudo crear el venv (falta python3-venv?): generando el dataset dentro de Docker"; \
		rm -rf $(VENV); \
		$(MAKE) --no-print-directory data-docker; \
	fi

data-local: venv ## Genera el dataset con el venv local
	$(VPY) data_generator/generate.py $(GEN_ARGS)

data-docker:     ## Genera el dataset dentro de un contenedor python:3.12-slim
	docker run --rm -u "$$(id -u):$$(id -g)" -e HOME=/tmp -e PIP_DISABLE_PIP_VERSION_CHECK=1 \
		-v "$(CURDIR)":/work -w /work python:3.12-slim \
		sh -c "pip install -q --no-warn-script-location --target /tmp/deps -r data_generator/requirements.txt \
		       && PYTHONPATH=/tmp/deps python data_generator/generate.py $(GEN_ARGS)"

source-up: env   ## Levanta el ORIGEN (Hive Metastore + HiveServer2)
	$(SRC) up -d --build --wait

source-up-impala: env  ## Levanta el ORIGEN incluyendo Impala (solo amd64)
	$(SRC) --profile impala up -d --build --wait

hue-up: env      ## Levanta Hue (UI web del ORIGEN) en http://localhost:8888
	$(SRC) --profile hue up -d --build

hue-down:        ## Detiene solo Hue
	$(SRC) --profile hue stop hue hue-postgres

source-load:     ## Crea tablas en el ORIGEN y carga el dataset
	bash scripts/source_load.sh

source-down:     ## Detiene el ORIGEN (incluye Impala y Hue)
	$(SRC) --profile impala --profile hue down

target-up: env   ## Levanta el DESTINO (Trino + Iceberg + SeaweedFS)
	$(TGT) up -d

target-init:     ## Crea los esquemas destino en Trino
	bash scripts/target_init.sh

target-down:     ## Detiene el DESTINO
	$(TGT) down

up: source-up target-up  ## Levanta origen y destino
down: source-down target-down  ## Detiene origen y destino

beeline:         ## Consola SQL de Hive (origen)
	$(SRC) exec hiveserver2 /opt/hive/bin/beeline -u jdbc:hive2://localhost:10000/ -n hive

trino:           ## Consola SQL de Trino (destino)
	$(TGT) exec trino trino --catalog iceberg

# ----------------------------- Motor agentico -----------------------------
AGENT := docker compose --env-file .env -f docker/agent/docker-compose.yml
NIM   := docker compose --env-file .env -f docker/nim/docker-compose.yml
# Argumentos extra para el agente, p. ej.: make agent-run ARGS="--no-llm --rows 500"
ARGS  ?=

nim-up: env      ## Levanta el LLM NVIDIA NIM (requiere NIM_IMAGE y NGC_API_KEY)
	$(NIM) up -d

nim-down:        ## Detiene el NIM
	$(NIM) down

agent-build: env ## Construye la imagen del motor agentico
	$(AGENT) build

agent-probe: agent-build  ## Detecta conectividad a CDP, VAST/Trino y NIM
	$(AGENT) run --rm agent probe

agent-run: agent-build  ## Pipeline completo con config/agent.yaml (VAST real; auto online/offline)
	mkdir -p out && $(AGENT) run --rm agent run $(ARGS)

agent-run-offline: agent-build  ## Pipeline 100% offline sobre el paquete de entrada (INPUT_BUNDLE_HOST)
	mkdir -p out && $(AGENT) run --rm agent run --source-mode offline --target-mode offline --bundle /app/input_bundle $(ARGS)

agent-run-local: agent-build  ## Pipeline contra la emulacion local (Hive docker -> Trino/Iceberg docker)
	mkdir -p out && $(AGENT) run --rm agent --config config/agent.local.yaml run $(ARGS)

agent-ask: agent-build  ## Modo agente interactivo: make agent-ask Q="traduce ..."
	$(AGENT) run --rm agent ask "$(Q)"

agent-test: agent-build  ## Pruebas del motor (sin CDP/VAST; NIM simulado)
	$(AGENT) run --rm --entrypoint python agent -m unittest discover -s tests -v

clean:           ## Borra contenedores, VOLUMENES y datos generados
	$(SRC) --profile impala --profile hue down -v
	$(TGT) down -v
	rm -rf data/source
