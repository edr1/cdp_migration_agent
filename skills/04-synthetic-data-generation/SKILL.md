---
name: synthetic-data-generation
description: Genera datos sinteticos realistas y reproducibles a partir del esquema migrado (IR), con integridad referencial, distribuciones no uniformes, coherencia por fila y corrupcion controlada documentada, para validar la migracion en VAST (automatica o manualmente).
phase: P4
tools: [SyntheticGenerator, infer_semantic, write_outputs, render_inserts, synthetic_preview]
inputs: Inventory (tipos VAST resueltos), synthetic.* en agent.yaml
outputs: 05_synthetic/<db>/<tabla>/{part-00000.parquet, insert.sql, data.csv}, manifest.json, LOAD_ORDER.sql
references: [providers-and-corruption.md]
adapted_from: databricks-solutions/vibe-coding-workshop-template · skills/bronze/01-faker-data-generation
---

# Skill: Generacion de datos sinteticos para validar la migracion

Adaptacion del patron *faker-data-generation* de Databricks (bronze) a un destino VAST/Trino y a un
esquema que **no se conoce de antemano** (se deriva del IR del origen).

## Principios (obligatorios)
1. **Reproducible**: una sola semilla (`synthetic.seed`) para `random` y `Faker.seed_instance`.
2. **Realista**: nada de distribuciones uniformes para valores de negocio. Categorias con pesos tipo Zipf,
   montos log-normales, contadores Poisson, edades exponenciales; fines de semana con 40% del volumen.
3. **Integridad referencial**: catalogos/padres primero (`order()`); las columnas hijas toman valores del
   padre (`synthetic.relationships`, con `weights` opcionales); columnas homonimas en distintas tablas
   comparten un pool (joins validos entre `db_production` y `db_consulting`).
4. **Coherencia por fila**: la fecha DATE = fecha del TIMESTAMP de evento; semana ISO y mes se derivan de
   esa fecha; columnas de auditoria/carga > evento; correo derivado del nombre.
5. **Valido primero, corrupcion despues**: con `corruption_rate > 0` cada registro corrupto se registra en
   `manifest.json -> corruptions` con la validacion que debe detectarlo (corruption -> expectation).
6. **Solo datos crudos**: no generar agregados (totales, promedios); eso se calcula en consultas.
7. **Respeta limites VAST**: lotes INSERT < 4.5 MB (limite 5 MB por sentencia), tipos VAST en Parquet
   (TIMESTAMP en microsegundos, DECIMAL con precision <= 38).

## Flujo
1. Para cada columna resolver la **semantica**: `column_overrides` > LLM (opcional, lista cerrada) >
   heuristica por nombre/tipo (`infer_semantic`).
2. Determinar volumen: `rows_per_table` > catalogo (n = valores del override) > `rows_default`.
3. Generar en orden padres -> hijos -> tablas `vw_*`; aplicar coherencia; aplicar corrupcion.
4. Escribir Parquet + `insert.sql` (+ CSV opcional) y `manifest.json` con el **perfil esperado**
   (filas, nulos, min/max, distintos) que la fase P5 convierte en validaciones.

## Uso del LLM
Solo para clasificar columnas ambiguas en la lista cerrada de semanticas. El LLM **no genera datos**
(costo, reproducibilidad y riesgo de PII real).

## Checklist
- [ ] Semilla fija y documentada en el manifest
- [ ] Ninguna columna de negocio con distribucion uniforme
- [ ] FK sin huerfanos (salvo corrupcion documentada)
- [ ] Fechas dentro de `start_date..end_date`; coherencia evento/carga
- [ ] Cada corrupcion con su expectation
- [ ] INSERT ejecutables en Trino (literales DATE/TIMESTAMP/DECIMAL tipados)

## Anti-patrones
- Valores uniformes, datos "planos" sin estacionalidad, columnas agregadas.
- Corromper antes de generar el registro valido o sin documentar la expectation.
- Usar datos reales del origen como semilla de Faker (riesgo de PII).
