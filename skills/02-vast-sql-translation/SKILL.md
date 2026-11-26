---
name: vast-sql-translation
description: Traduce DDL, DML y DQL de Hive/Impala a SQL Trino para VAST Database aplicando la matriz de equivalencia oficial (VAST 5.5) y sqlglot; marca lo no traducible para revision LLM/humana.
phase: P2
tools: [Translator.translate_table, Translator.translate_statement, lookup_matrix, translate_sql, validate_trino_syntax]
inputs: Inventory, config/equivalence_matrix.yaml, naming destino (vast | plain)
outputs: 01_ddl/*.sql, 02_dml/*.sql, 03_dql/*.sql, review/llm_suggestions.sql, findings
references: [equivalence-matrix.md]
---

# Skill: Traduccion Hive/Impala -> VAST Database (via Trino)

## Principio: deterministico primero
1. **Reglas** (matriz + sqlglot hive->trino) resuelven tipos, clausulas y funciones.
2. Lo que queda `severity: manual` pasa al **LLM** (Nemotron via Ollama) que propone una traduccion en JSON.
3. La propuesta se valida con `validate_trino_syntax` y se guarda en `review/llm_suggestions.sql`.
   **Nunca** se mezcla con los scripts principales sin aprobacion humana.

## Nombres en VAST
`vast."<bucket>/<schema>".<tabla>` — el schema VAST incluye el bucket entre comillas dobles.
Cada base Hive se convierte en un schema: `CREATE SCHEMA IF NOT EXISTS vast."<bucket>/<db>"`.

## Reglas clave (ver references/equivalence-matrix.md)
- Tipos: STRING->VARCHAR, INT->INTEGER, FLOAT->REAL, TIMESTAMP->TIMESTAMP(6), BINARY->VARBINARY,
  ARRAY<T>->ARRAY(T), MAP<K,V>->MAP(K,V), STRUCT<a:T>->ROW(a T). CHAR(n)/VARCHAR(n) -> VARCHAR (warning).
  UNIONTYPE e INTERVAL: sin equivalente (manual).
- DDL: se eliminan STORED AS, ROW FORMAT, LOCATION, TBLPROPERTIES. `PARTITIONED BY` -> columnas normales +
  `WITH (partitioning = ARRAY[...])` (max 4 llaves, solo al crear). Buckets -> `bucket(col, n)`.
  `SORTED BY`/`SORT BY` -> `sorted_by` (max 4, no modificable). Si la tabla esta particionada, sorted_by va
  dentro del `WITH` del CREATE (VAST solo permite ordenado+particionado al crear).
- Restricciones NOT NULL/PK/UNIQUE/DEFAULT: no soportadas -> se eliminan con warning.
- DML: `INSERT OVERWRITE` -> `START TRANSACTION; DELETE [WHERE particion]; INSERT ...; COMMIT`.
  `PARTITION (p='v')` estatica -> literal agregado al SELECT y lista de columnas explicita.
  `TRUNCATE` -> `DELETE FROM`. `LOAD DATA`, `UPSERT`, multi-insert `FROM ... INSERT`: manual.
- DQL: hints de Impala (`[SHUFFLE]`, `/* +... */`, `STRAIGHT_JOIN`) se eliminan; funciones Hive se
  convierten (nvl->coalesce, collect_set->array_agg(distinct), LATERAL VIEW explode->CROSS JOIN UNNEST,
  RLIKE->regexp_like, size->cardinality, get_json_object->json_extract_scalar).
- **Conversiones implicitas**: Hive compara `fecha = '2026-01-01'`; Trino no. El traductor reescribe
  literales contra columnas DATE/TIMESTAMP/numericas/VARCHAR usando los tipos del inventario.
- Sentencias de sesion (SET, USE, ADD JAR, INVALIDATE METADATA, REFRESH, COMPUTE STATS, MSCK): se eliminan.
- UDFs (`CREATE FUNCTION`, `reflect`, `java_method`): manual.

## Instrucciones para el LLM (propuestas de revision)
- Responde SOLO JSON `{"sql": [...], "explanation": "...", "confidence": 0-1}`.
- Usa exclusivamente sintaxis Trino y nombres `vast."<bucket>/<schema>".<tabla>`.
- No uses: CTAS en el Query Engine nativo, `ON CONFLICT`, `INSERT OVERWRITE`, `ALTER COLUMN SET DATA TYPE`.
- Upsert: `MERGE INTO ... USING ... WHEN MATCHED THEN UPDATE ... WHEN NOT MATCHED THEN INSERT ...`.
- Cambio de tipo: `ADD COLUMN nueva`, `UPDATE ... SET nueva = CAST(vieja AS tipo)`, `DROP COLUMN vieja`,
  `RENAME COLUMN nueva TO vieja`.
- LOAD DATA: proponer `INSERT INTO vast... SELECT * FROM hive.<db>.<tabla>` (Trino federado) y
  documentar la alternativa de importar Parquet.
- Si no hay equivalente seguro, devuelve `"sql": []` y explica por que. Nunca inventes funciones.
