---
name: source-discovery
description: Construye el inventario canonico (IR) del origen Cloudera, ya sea consultando HiveServer2/Ranger (online) o interpretando un paquete de DDL, DML, DQL y politicas Ranger exportadas (offline).
phase: P1
tools: [source_online.discover, source_offline.ingest_bundle, split_statements, classify, table_from_ddl, parse_ranger_export]
inputs: conexion HiveServer2 + Ranger REST (online) | carpeta input_bundle (offline)
outputs: Inventory {databases, tables, statements, policies}
---

# Skill: Descubrimiento del origen (Cloudera CDP)

## Camino unico de parsing
El modo online **no** tiene un parser propio: obtiene el texto con `SHOW CREATE TABLE` y lo pasa por el
mismo parser que el modo offline. Asi la traduccion es identica en ambos modos.

## Modo offline: paquete de entrada
```
input_bundle/
  ddl/*.sql      CREATE DATABASE/TABLE/VIEW, ALTER, DROP (Hive o Impala)
  dml/*.sql      INSERT [OVERWRITE], LOAD DATA, UPDATE, DELETE, MERGE, UPSERT, TRUNCATE
  dql/*.sql      SELECT / WITH (reportes, consumidores BI)
  ranger/*.json  export de politicas (Ranger Admin -> Export, o REST /service/public/v2/api/policy)
```
- Archivos con `impala` en el nombre se marcan como motor Impala (afecta hints y SORT BY).
- Se aceptan comentarios `--`, `/* */` y lineas `#` (frecuentes en exports de CDP).
- `USE db` cambia la base por defecto de las sentencias siguientes del mismo archivo.

## Clasificacion
Cada sentencia recibe `kind` (DDL | DML | DQL | SESSION | UNKNOWN) y `operation`
(CREATE_TABLE, CTAS, INSERT_OVERWRITE, MULTI_INSERT, LOAD_DATA, UPSERT, ...).
`UNKNOWN` genera hallazgo `manual`.

## Reglas
1. Tablas definidas dos veces: se conserva la ultima y se emite `inventory.duplicate_table` (warning).
2. Columnas de particion (`PARTITIONED BY`) se agregan al final de la tabla con `is_partition=true`.
3. `CLUSTERED BY ... SORTED BY ... INTO n BUCKETS` y `SORT BY` (Impala) se guardan en el IR para la
   fase de traduccion.
4. Politicas Ranger: `policyType` 0=access, 1=masking, 2=rowfilter. Politicas deshabilitadas se
   inventarian pero no se migran.
5. En modo online se agregan conteos de filas (`row_count`) para validar la migracion de datos reales.
