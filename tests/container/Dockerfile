ARG PYTHON_VERSION=3.12
FROM koalaman/shellcheck:v0.11.0 AS shellcheck

FROM node:26.12.1 AS markdown
WORKDIR /opt/markdown
COPY tests/container/package.json tests/container/package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund

FROM python:${PYTHON_VERSION}

COPY --from=shellcheck /bin/shellcheck /usr/local/bin/shellcheck
COPY --from=markdown /usr/local/bin/node /usr/local/bin/node
COPY --from=markdown /opt/markdown /opt/markdown
COPY .python-version requirements-dev.txt /opt/check/
RUN python -m venv /opt/check/venv \
    && /opt/check/venv/bin/python -m pip install --no-cache-dir --disable-pip-version-check \
        -r /opt/check/requirements-dev.txt

COPY . /opt/project
ENV PATH=/opt/check/venv/bin:/opt/markdown/node_modules/.bin:${PATH}
ENV PYTHONPYCACHEPREFIX=/scratch/pycache RUFF_CACHE_DIR=/scratch/ruff
