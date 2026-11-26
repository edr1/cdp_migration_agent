# Semanticas, proveedores y catalogo de corrupcion

## Semanticas (infer_semantic)
| Semantica | Patron de nombre (regex) | Generador |
|---|---|---|
| email | correo, email, mail | derivado del nombre de la fila o `Faker.email` |
| person_name | nm_empleado, nm_jefe, nm_cliente | nombre + 2 apellidos (es_MX), mayusculas |
| username | usr, user, usuario, login | `Faker.user_name` (pool compartido) |
| ipv4 | ipv4, ip_address, direccion_ip | `Faker.ipv4_private` |
| domain | dominio, domain | 4 dominios corporativos |
| hostname | computadora, equipo, host, servidor | prefijo MXL/MAC/SRV/LNX + 6 digitos (pool compartido) |
| uuid | uuid, guid, id_instancia | UUID deterministico |
| code | cve_, id_, cod_, clave_ | 6 digitos; en catalogos 0..n |
| iso_week / year_month | semana / mes_ | derivados de la fecha del evento |
| status | estado, estatus, status | presets (CUMPLE/NO CUMPLE/NO APLICA, ACTIVO/BAJA/LICENCIA, OK/KO) |
| flag | indicador, flag, is_, es_ | S/N 15/85 |
| priority | prioridad | ALTA/MEDIA/BAJA 15/50/35 |
| job | posicion, puesto, cargo | `Faker.job` |
| count | contador, cantidad, num_ (numerico) | Poisson(8) |
| age | age, edad, antiguedad (numerico) | exponencial media 20 |
| amount | monto, importe, precio, total (numerico) | log-normal(7, 0.8) |
| date / timestamp | por tipo | rango configurado, fines de semana al 40% |
| category | resto de VARCHAR | 8 categorias con pesos Zipf(1.1) |

## Corrupcion -> expectation (validacion que debe fallar)
| Corrupcion | Aplica a | Valor inyectado | Expectation |
|---|---|---|---|
| invalid_format | email | `not-an-email` | `col LIKE '%@%.%'` |
| out_of_range | count/number/amount/age | `-1` | `col >= 0` |
| future_date | date/timestamp | 2099-01-01 | `col <= current_date` |
| invalid_fk | columna hija de relationship | `999999` | valor existe en la tabla padre |
| null_required | cualquier otra | `NULL` | `col IS NOT NULL` |

Recomendacion de tasas (igual que el patron Databricks): dev 0.10, QA 0.05, validacion de migracion 0.0.
