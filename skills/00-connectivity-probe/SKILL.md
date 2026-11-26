---
name: connectivity-probe
description: Detecta si el motor tiene acceso a Cloudera CDP (HiveServer2/Impala/Ranger), a VAST Database (via Trino) y al LLM NIM, y decide el modo de operacion (online/offline) de cada lado.
phase: P0
tools: [tcp_probe, probe_hive, probe_trino, NIMClient.available]
inputs: config/agent.yaml (source.mode, target.mode, hosts)
outputs: modes {source, target, probes[]}, cliente Trino si el destino esta online
---

# Skill: Deteccion de conectividad y modo de operacion

## Objetivo
Decidir, antes de cualquier otra fase, **que caminos** usa el motor:

| Origen CDP | Destino VAST | Camino |
|---|---|---|
| online  | online  | Descubrimiento directo + despliegue y validacion automaticos en VAST |
| online  | offline | Descubrimiento directo + paquete de scripts para ejecucion manual en VAST |
| offline | online  | Ingesta del paquete de entrada + despliegue/validacion en VAST |
| offline | offline | Ingesta del paquete de entrada + paquete de scripts + datos sinteticos para validacion manual |

## Reglas
1. `mode: online|offline` en la configuracion **fuerza** el modo. `auto` prueba conectividad.
2. Online exige mas que un puerto abierto: HiveServer2 debe responder `SHOW DATABASES` y Trino debe
   listar el catalogo configurado (`vast`) en `SHOW CATALOGS`.
3. Si se fuerza `online` y la prueba falla, **detener** con error explicito (no degradar en silencio).
4. El LLM es opcional: si el NIM no responde, el motor continua en modo deterministico y lo registra
   como hallazgo `llm.unavailable`. Ninguna fase depende del LLM para producir un resultado valido.
5. Nunca registrar credenciales en eventos o reportes.

## Salida esperada
`report.json -> modes` con el resultado de cada prueba (`reachable`, `detail`).
