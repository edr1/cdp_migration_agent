---
name: ranger-policy-mapping
description: Convierte politicas de Apache Ranger (hive/impala) en un plan de acceso para VAST Database (identity policies, reglas de columna, mascaras y filtros de fila) y en reglas file-based de Trino equivalentes.
phase: P3
tools: [ranger.translate_policies, lookup_matrix(security)]
inputs: Inventory.policies (export JSON o Ranger REST)
outputs: 04_security/vast_access_plan.json, 04_security/trino_rules.json, 04_security/vast_catalog_security.properties
---

# Skill: Politicas Ranger -> control de acceso VAST

## Modelo destino
VAST 5.5 **no documenta GRANT/REVOKE**. El acceso se controla con:
- **Identity policies** en VMS (nivel read / write / admin sobre bucket/schema/tabla).
- **Reglas de fila/columna** (column allow/deny, column mask, row filter) que Trino aplica si el catalogo
  `vast` tiene `enable_access_control`, `enable_row_column_security` y `enable_end_user_impersonation`.
- El usuario de sesion Trino se envia como end user y **debe coincidir** con un login VAST.

## Mapeo
| Ranger | Salida |
|---|---|
| access: select, read, refresh | level `read` |
| access: update, write | level `write` |
| access: create, drop, alter, index, lock, all | level `admin` |
| denyPolicyItems | identity policy `effect: deny`; con columnas -> `column_deny` |
| resources.column != * | `column_allow` / `column_deny` |
| dataMaskPolicyItems | `column_mask` con la expresion Trino de la matriz (`security.masks`) |
| rowFilterPolicyItems | `row_filter` con `filterExpr` (validar sintaxis Trino) |

## Reglas
1. Politicas `isEnabled=false` no se migran (hallazgo info).
2. `allowExceptions` / `denyExceptions` no tienen equivalente directo -> **manual**.
3. Grupo `public` -> warning: definir explicitamente "todos los usuarios" en VAST.
4. Mascaras `CUSTOM` y filtros de fila vienen en sintaxis Hive: siempre warning para validarlos en Trino
   (p. ej. `split(x,'@')[1]` en Hive es base 0; en Trino los arrays son base 1).
5. El plan VAST es una **especificacion para revision humana**: el motor no modifica VMS.
6. `trino_rules.json` sigue el formato de file-based access control de Trino (catalogs/schemas/tables con
   privileges, columns[mask|allow], filter) y sirve para el ambiente de emulacion o si Trino es el punto de
   control.
