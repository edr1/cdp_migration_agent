# Hue + cliente Python de Trino.
# Hue 4.11 no tiene conector nativo "trino"; se usa su conector SQLAlchemy con el
# dialecto trino:// que trae el paquete "trino". Se instala sin el extra [sqlalchemy]
# para no alterar la version de SQLAlchemy que ya usa Hue.
#
# - Se usa "python -m pip" (el virtualenv de Hue no siempre trae el script bin/pip);
#   si pip no esta instalado se instala con ensurepip.
# - pip elige sola la ultima version de "trino" compatible con el Python de Hue.
# - Si este paso falla con "exec format error", la imagen de Hue no tiene la arquitectura
#   del host: registra la emulacion con
#   docker run --privileged --rm tonistiigi/binfmt --install amd64
ARG HUE_VERSION=4.11.0
FROM gethue/hue:${HUE_VERSION}

USER root
RUN set -eux; \
    uname -m; \
    PY=/usr/share/hue/build/env/bin/python; \
    if [ ! -x "$PY" ]; then PY="$(ls /usr/share/hue/build/env/bin/python3* | head -n1)"; fi; \
    "$PY" --version; \
    "$PY" -m pip --version || "$PY" -m ensurepip --upgrade; \
    "$PY" -m pip install --no-cache-dir trino; \
    "$PY" -c "import trino.sqlalchemy; print('trino dialect OK')"
USER hue
