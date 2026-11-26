---
name: vast-deploy-validate
description: Despliega el DDL y los datos en VAST Database via Trino cuando hay conectividad (con ciclo de reparacion por LLM) y genera/ejecuta el paquete de validacion (conteos, nulos, min/max, distintos) contra resultados esperados.
phase: P5
tools: [TrinoTarget.execute, build_checks, compare, render_checks_sql, run_trino_query]
inputs: 01_ddl/*.sql, 05_synthetic/*, manifest.json (o perfil del origen online)
outputs: 06_validation/{checks.sql, expected_results.json, results.json, deploy_results.json}
---

# Skill: Despliegue y validacion en VAST

## Modo destino online
1. Ejecutar en orden: `CREATE SCHEMA` -> `CREATE TABLE` (+ `ALTER ... SET PROPERTIES sorted_by`) -> vistas.
2. Si una sentencia falla, el LLM recibe **SQL + error** y devuelve `{"sql": "..."}` corregido.
   Maximo `llm.max_repair_attempts` intentos; cada reparacion exitosa se registra como hallazgo
   `deploy.llm_repaired` para revision.
3. Cargar `insert.sql` por tabla en el orden de `LOAD_ORDER.sql`.
4. Ejecutar `checks.sql` y comparar con `expected_results.json` (tolerancia de formato en
   timestamps/decimales).

## Modo destino offline
Generar el mismo paquete para ejecucion manual en VAST:
`trino --catalog vast -f 06_validation/checks.sql` y comparar contra `expected_results.json`.

## Reglas de reparacion (LLM)
- Errores tipicos VAST: tipo no soportado (usar la matriz), `Invalid Argument` en combinaciones
  prohibidas (sorted + particion despues de crear; mas de 4 llaves), nombres sin `"<bucket>/<schema>"`,
  `CTAS` en Query Engine, `ON CONFLICT`.
- Nunca cambiar la semantica: no eliminar columnas ni filtros para "hacer que funcione".
- Las tools con efectos (ejecucion en VAST) solo existen con destino online; el modo agente solo
  permite consultas de lectura.

## Validaciones generadas
| Tipo | SQL | Esperado |
|---|---|---|
| row_count | `SELECT COUNT(*)` | filas generadas / conteo del origen |
| null_count | `COUNT(*) WHERE col IS NULL` | nulos por columna |
| min / max | `CAST(MIN(col) AS VARCHAR)` | rango numerico/fecha |
| distinct_count | `COUNT(DISTINCT col)` | cardinalidad de claves/categorias |
