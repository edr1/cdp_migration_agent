# Matriz de equivalencia Hive/Impala -> VAST Database (Trino)

Fuente de verdad ejecutable: `config/equivalence_matrix.yaml`. Esta tabla es la version legible.
Fuente: VAST Cluster 5.5, SQL reference y Trino integration (consultado 2026-11-25).
`Verif.` = respaldado explicitamente por la doc VAST; `No` = comportamiento Trino estandar a validar.

## Tipos
| Hive / Impala | VAST (Trino) | Sev. | Verif. | Nota |
|---|---|---|---|---|
| STRING | VARCHAR | ok | Si | |
| VARCHAR(n) | VARCHAR | warning | No | la doc solo lista VARCHAR sin longitud |
| CHAR(n) | VARCHAR | warning | No | se pierde el padding |
| TINYINT / SMALLINT / INT / BIGINT | TINYINT / SMALLINT / INTEGER / BIGINT | ok | Si | |
| FLOAT / DOUBLE | REAL / DOUBLE | ok | Si | |
| DECIMAL(p,s) | DECIMAL(p,s) | ok | Si | p <= 38 |
| BOOLEAN | BOOLEAN | ok | Si | |
| DATE | DATE | ok | Si | |
| TIMESTAMP | TIMESTAMP(6) | warning | Si | nanosegundos -> microsegundos |
| BINARY | VARBINARY | ok | Si | |
| ARRAY<T> / MAP<K,V> / STRUCT<..> | ARRAY(T) / MAP(K,V) / ROW(..) | ok | Si | no particion/orden; no en vistas |
| UNIONTYPE / INTERVAL | — | manual | Si | sin equivalente |

## DDL
| Origen | VAST | Sev. |
|---|---|---|
| CREATE DATABASE db | CREATE SCHEMA vast."bucket/db" | ok |
| STORED AS / ROW FORMAT / SERDE / LOCATION / TBLPROPERTIES | se eliminan | info |
| EXTERNAL | tabla administrada; migrar datos | warning |
| PARTITIONED BY (c T) | columna c + WITH (partitioning = ARRAY['c']) (max 4, solo CREATE) | warning |
| CLUSTERED BY (c) INTO n BUCKETS | 'bucket(c, n)' en partitioning | warning |
| SORTED BY / SORT BY | sorted_by = ARRAY[..] (max 4; inmutable) | warning |
| NOT NULL / PK / UNIQUE / CHECK / DEFAULT | no soportado | warning |
| CTAS | soportado via Trino | ok |
| CREATE VIEW | CREATE OR REPLACE VIEW (sin tipos anidados, nombres completos) | warning |
| MATERIALIZED VIEW | no documentado | manual |
| ALTER ... ADD COLUMNS | ALTER TABLE ... ADD COLUMN (una por sentencia) | ok |
| ALTER ... CHANGE COLUMN (tipo) | ADD + UPDATE CAST + DROP + RENAME | manual |
| ALTER ... ADD/DROP PARTITION, SET LOCATION/SERDE | no aplica | info |

## DML
| Origen | VAST | Sev. |
|---|---|---|
| INSERT INTO ... SELECT / VALUES | igual (lotes < 5 MB) | ok |
| INSERT OVERWRITE [PARTITION] | DELETE [WHERE particion] + INSERT en transaccion | warning |
| PARTITION (p='v') estatica | literal en el SELECT + lista de columnas | warning |
| LOAD DATA INPATH | Trino federado o import Parquet (vast.import_data) | manual |
| UPDATE / DELETE | igual (no actualizar columnas sorted_by) | ok/warning |
| MERGE | MERGE via Trino | warning |
| TRUNCATE | DELETE FROM | info |
| UPSERT (Impala/Kudu) | MERGE o DELETE+INSERT | manual |
| FROM t INSERT ... INSERT ... | dividir en INSERT individuales | manual |

## Seguridad (Ranger -> VAST)
| Ranger | VAST |
|---|---|
| Access policy (select/update/create/drop/alter/all) | Identity policy VMS: read / write / admin |
| Deny items | Identity policy con efecto deny / column deny |
| Column-level access | Column allow/deny rules |
| Masking (MASK, SHOW_LAST_4, HASH, NULL, CUSTOM) | Column mask (expresion Trino) |
| Row filter | Row filter de VAST (aplicado por Trino) |
| Usuarios/grupos | Identidades VAST (usuario de sesion Trino = login VAST) |
| allow/deny exceptions | sin equivalente directo (manual) |

Requisito del catalogo Trino: `enable_access_control=true`, `enable_row_column_security=true`,
`enable_end_user_impersonation=true`. VAST 5.5 no documenta GRANT/REVOKE.
