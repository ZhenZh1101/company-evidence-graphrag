FROM python:3.12-slim AS dependencies

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN useradd --create-home --uid 1000 user
WORKDIR /home/user/app

COPY requirements.lock pyproject.toml ./
COPY ir_graphrag ./ir_graphrag
RUN python -m pip install -r requirements.lock

FROM dependencies

COPY --chown=user:user app.py ./
COPY --chown=user:user .streamlit ./.streamlit
COPY --chown=user:user deployment/workspaces.tar.gz /tmp/workspaces.tar.gz
RUN tar -xzf /tmp/workspaces.tar.gz --no-same-owner \
    && rm /tmp/workspaces.tar.gz \
    && chown -R user:user workspaces

USER user
EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7860/_stcore/health', timeout=5)"
CMD ["python", "-m", "streamlit", "run", "app.py", "--server.address", "0.0.0.0", "--server.port", "7860", "--server.headless", "true", "--browser.gatherUsageStats", "false"]
