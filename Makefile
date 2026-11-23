SHELL := /bin/bash
PYTHON ?= python3
VENV   := .venv
VPY    := $(VENV)/bin/python
SRC    := docker compose --env-file .env -f docker/cloudera/docker-compose.yml
TGT    := docker compose --env-file .env -f docker/trino/docker-compose.yml

.PHONY: help env venv data source-up source-up-impala source-load source-down target-up target-init target-down up down clean beeline trino

help:            ## Lista de comandos
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-18s %s\n", $$1, $$2}'

env:             ## Crea .env desde .env.example (si no existe)
	@test -f .env || cp .env.example .env

venv:            ## Entorno Python para el generador
	$(PYTHON) -m venv $(VENV) && $(VPY) -m pip install -q -r data_generator/requirements.txt

data: venv       ## Genera el dataset sintetico (SCALE=1.0 SEED=42)
	$(VPY) data_generator/generate.py --out data/source --scale $${SCALE:-1.0} --seed $${SEED:-42}

source-up: env   ## Levanta el ORIGEN (Hive Metastore + HiveServer2)
	$(SRC) up -d --build --wait

source-up-impala: env  ## Levanta el ORIGEN incluyendo Impala (solo amd64)
	$(SRC) --profile impala up -d --build --wait

source-load:     ## Crea tablas en el ORIGEN y carga el dataset
	bash scripts/source_load.sh

source-down:     ## Detiene el ORIGEN
	$(SRC) --profile impala down

target-up: env   ## Levanta el DESTINO (Trino + Iceberg + MinIO)
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

clean:           ## Borra contenedores, VOLUMENES y datos generados
	$(SRC) --profile impala down -v
	$(TGT) down -v
	rm -rf data/source
