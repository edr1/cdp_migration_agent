---
name: migration-packaging
description: Consolida los artefactos de todas las fases en un paquete de migracion versionable (scripts ordenados, runbook, reporte, hallazgos y cola de revision manual).
phase: P6
tools: [MigrationPipeline.p6_package]
inputs: RunContext completo
outputs: RUNBOOK.md, 00_report/{report.md, report.json, findings.csv}, review/manual_items.md
---

# Skill: Empaquetado y reporte

## Estructura del paquete
```
out/<run>/
  RUNBOOK.md                      pasos de ejecucion en VAST
  00_report/report.md|json        resumen ejecutivo, modos, inventario, hallazgos
  00_report/findings.csv          todos los hallazgos (severidad, regla, objeto, verificado)
  01_ddl/                         esquemas, tablas, vistas, ALTER
  02_dml/  03_dql/                sentencias traducidas por archivo de origen
  04_security/                    plan de acceso VAST + reglas Trino
  05_synthetic/                   Parquet, INSERT, manifest
  06_validation/                  checks.sql, expected_results.json, results.json
  review/                         manual_items.md, llm_suggestions.sql
```

## Reglas
1. El reporte separa **resuelto por regla**, **propuesto por LLM** y **pendiente humano**.
2. Cada hallazgo indica si la regla esta `verified` contra la doc VAST.
3. Nunca incluir credenciales ni datos reales del origen en el paquete.
4. El paquete es autocontenido: puede copiarse a un servidor con acceso a VAST y ejecutarse con el
   cliente Trino sin el motor.
