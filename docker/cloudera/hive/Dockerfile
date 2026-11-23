# Imagen Hive (el motor Hive que incluye Cloudera CDP) + driver JDBC de PostgreSQL
# para el Hive Metastore. La usan tanto el metastore como HiveServer2.
# La configuracion se pasa por variables de entorno (SERVICE_OPTS) en docker-compose,
# conservando el hive-site.xml que trae la imagen oficial.
ARG HIVE_VERSION=4.0.1
FROM apache/hive:${HIVE_VERSION}

ARG PG_JDBC_VERSION=42.7.4
USER root
ADD --chmod=644 https://repo1.maven.org/maven2/org/postgresql/postgresql/${PG_JDBC_VERSION}/postgresql-${PG_JDBC_VERSION}.jar /opt/hive/lib/postgres.jar
USER hive
