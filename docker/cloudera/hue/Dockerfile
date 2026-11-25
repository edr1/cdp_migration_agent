# Hue + cliente Python de Trino.
# Hue 4.11 no tiene conector nativo "trino"; se usa su conector SQLAlchemy con el
# dialecto trino:// que trae el paquete "trino". Se instala sin el extra [sqlalchemy]
# para no alterar la version de SQLAlchemy que ya usa Hue.
ARG HUE_VERSION=4.11.0
FROM gethue/hue:${HUE_VERSION}

USER root
RUN /usr/share/hue/build/env/bin/pip install --no-cache-dir trino \
 && /usr/share/hue/build/env/bin/python -c "import trino.sqlalchemy; print('trino dialect OK')"
USER hue
