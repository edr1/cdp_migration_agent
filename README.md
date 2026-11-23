# CDP → Trino Migration Agent

Agente de IA para migrar datos desde **Cloudera (origen: Hive / Impala)** hacia **Trino (destino: Iceberg)**,
pensado para ejecutarse en una **NVIDIA DGX Spark** usando un modelo servido con **NVIDIA NIM**.

> **Fase 1:** ambiente local de origen y destino en Docker + dataset sintético.
> El agente (orquestación + NIM) se construye en la siguiente fase sobre esta base.

## Arquitectura

```
            red docker: cdp-migration-net
┌──────────── ORIGEN "Cloudera" ────────────┐     ┌──────────── DESTINO Trino ────────────┐
│ hms-postgres ── hive-metastore (9083)     │     │ trino (8080) ── catalogo "iceberg"    │
│                   │                       │     │    │                    │             │
│ hiveserver2 (10000)   impala* (21050)     │ ==> │ iceberg-postgres     minio (9000/9001)│
│   db_production / db_consulting (Parquet) │     │  (catalogo JDBC)     bucket warehouse │
└───────────────────────────────────────────┘     └───────────────────────────────────────┘
 * Impala es opcional (perfil "impala")
```

### ¿Por qué no la imagen oficial de Cloudera?
Cloudera no publica una imagen Docker gratuita de CDP; la antigua `cloudera/quickstart` (CDH 5) está
descontinuada y es **solo x86_64**, mientras que la DGX Spark es **ARM64 (Grace)**. Por eso el origen se
emula con los mismos motores open source que trae CDP Runtime:

| Componente CDP | Emulación local | Arquitecturas |
|---|---|---|
| Hive Metastore | `apache/hive` (metastore) + PostgreSQL | amd64 / arm64* |
| Hive (HiveServer2) | `apache/hive` (hiveserver2) | amd64 / arm64* |
| Impala | `apache/impala` (statestored, catalogd, impalad) | **solo amd64** |

\* Verifica la disponibilidad arm64 de `apache/hive:${HIVE_VERSION}` en la DGX Spark (`docker manifest inspect apache/hive:4.0.1`).

Hive e Impala comparten el **mismo Hive Metastore** (igual que en CDP), así que las tablas de
`db_consulting` creadas vía HiveServer2 son visibles en Impala. En la DGX Spark puedes omitir Impala:
el agente leerá ambas bases por HiveServer2.

## Esquema de origen

DDL en [`sql/source/`](sql/source):

| Base | Tabla | Motor original | Filas (scale=1) |
|---|---|---|---|
| `db_production` | `cat_certificacion_valor_sensor` | Hive | 6 |
| `db_production` | `tbl_certificacion_logs` | Hive | 20,000 |
| `db_production` | `vw_certificacion_logs` | Hive | 20,000 |
| `db_consulting` | `tbl_sensores_equipos_cloud` | Impala | 30 |
| `db_consulting` | `tbl_equipos_cloud_certificacion` | Impala | 8,000 |
| `db_consulting` | `vw_equipos_cloud_certificacion` | Impala | 8,000 |

> ⚠️ **Ajuste a la definición original:** `db_consulting.vw_equipos_cloud_certificacion` venía definida dos veces
> con columnas distintas. Se mantuvo la versión resumida (17 columnas, con `os_tipo_norm`) con ese nombre y la versión
> detallada (40 columnas) se nombró **`tbl_equipos_cloud_certificacion`**, siguiendo la convención `tbl_`/`vw_`
> de `db_production`. También se convirtieron los comentarios `#------` a `--` (HiveQL válido).

## Dataset sintético

[`data_generator/generate.py`](data_generator/generate.py) genera un Parquet por tabla
(`data/source/<base>/<tabla>/part-00000.parquet`) más `data/source/manifest.json` con conteos y tipos,
útil para validar la migración. Características:

- **Coherencia referencial:** los `cve_estado_*` de los logs existen en el catálogo; las tablas `vw_*` se derivan
  1:1 de sus tablas `tbl_*`; empleados y equipos se comparten entre ambas bases.
- **Reglas de negocio:** sensores que no aplican al SO → `NO APLICA`; un equipo está `CERTIFICADO` sólo si todos sus
  sensores cumplen; `sumatoria` = sensores OK; `os_tipo` "sucio" (`Win10`, `Darwin`, `MAC`…) normalizado en `os_tipo_norm`.
- **Casos borde para la migración:** acentos y eñes (`es_MX`), NULLs, timestamps con microsegundos, tipos `INT`/`BIGINT`/`DATE`.
- Timestamps escritos como **INT96** (formato nativo Hive/Impala). Reproducible con `--seed`; volumen con `--scale`.

## Uso rápido (Linux / DGX Spark)

Requisitos: Docker + Compose v2, `make` y, opcionalmente, Python 3.10+ con `python3-venv`.
Si `python3-venv` no está instalado (Ubuntu sin `apt install python3.12-venv`), `make data` genera el
dataset automáticamente dentro de un contenedor `python:3.12-slim` (también disponible como `make data-docker`).

```bash
make env            # crea .env desde .env.example
make data           # genera el dataset (SCALE=5 make data para 5x)
make source-up      # Hive Metastore + HiveServer2  (make source-up-impala para incluir Impala)
make source-load    # crea bases/tablas y carga el dataset; muestra conteos
make target-up      # Trino + Iceberg + MinIO
make target-init    # crea iceberg.db_production y iceberg.db_consulting
```

Consolas: `make beeline` (Hive) · `make trino` (Trino) · `make help` para todos los comandos.
`make clean` elimina contenedores, **volúmenes** y datos generados.

### Endpoints

| Servicio | URL / conexión |
|---|---|
| HiveServer2 | `jdbc:hive2://localhost:10000/` (usuario `hive`, sin password) · UI http://localhost:10002 |
| Hive Metastore | `thrift://localhost:9083` |
| Impala (opcional) | `localhost:21050` (HS2) · UI http://localhost:25000 |
| Trino | http://localhost:8080 (usuario cualquiera) · catálogo `iceberg` |
| MinIO | API http://localhost:9000 · consola http://localhost:9001 |

Credenciales locales de desarrollo en `.env` (no versionado).

## Estructura

```
docker/cloudera/   compose del origen (Hive, HMS, Impala opcional)
docker/trino/      compose del destino + catálogo Iceberg
sql/source/        DDL, carga y validación del origen
sql/target/        esquemas destino
data_generator/    generador del dataset sintético
scripts/           carga del origen / inicialización del destino
```

## Siguiente fase: agente de migración con NVIDIA NIM
- Servir un LLM con NIM en la DGX Spark (contenedor NIM con API compatible OpenAI) en la misma red `cdp-migration-net`.
- Herramientas del agente: inspección de metadatos (HMS / `DESCRIBE FORMATTED`), mapeo de tipos Hive→Trino,
  generación de DDL Iceberg, copia de datos y validación (conteos/checksums contra `manifest.json`).
