ARG PYTHON_VERSION=3.12
FROM node:24.22.2 AS markdown
RUN npm install --prefix /opt/markdown --no-audit --no-fund --ignore-scripts markdownlint-cli2@0.24.0

FROM python:${PYTHON_VERSION}

COPY --from=markdown /usr/local/bin/node /usr/local/bin/node
COPY --from=markdown /opt/markdown /opt/markdown
COPY .python-version requirements-dev.txt /opt/check/
RUN test "$(python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')" = "$(cat /opt/check/.python-version)" \
    && python -m venv /opt/check/venv \
    && /opt/check/venv/bin/python -m pip install --no-cache-dir --disable-pip-version-check \
        -r /opt/check/requirements-dev.txt

COPY tests/container/check /opt/check/run
ENV PATH=/opt/check/venv/bin:/opt/markdown/node_modules/.bin:${PATH}
