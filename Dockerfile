FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONUTF8=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN pip install --no-cache-dir -r requirements.txt
RUN useradd --create-home --uid 10001 gridcast
COPY --chown=gridcast:gridcast backend backend
COPY --chown=gridcast:gridcast src src
COPY --chown=gridcast:gridcast scripts scripts
COPY --chown=gridcast:gridcast main.py .
RUN mkdir -p data models reports && chown -R gridcast:gridcast data models reports
USER gridcast
EXPOSE 5000
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--access-logfile", "-", "backend.app:create_app()"]
