FROM python:3.12-slim

# Run as an unprivileged user; /data holds the SQLite file and is the only writable path.
RUN useradd --system --create-home --uid 10001 ledger \
    && mkdir /data && chown ledger:ledger /data
WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn==23.0.0

COPY --chown=ledger:ledger app ./app
COPY --chown=ledger:ledger run.py .

ENV LEDGER_DB=/data/ledger.db
VOLUME /data
EXPOSE 5000
USER ledger

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=2)"

# One worker + threads: SQLite serializes writers, so extra processes add contention, not throughput.
CMD ["gunicorn", "-w", "1", "--threads", "8", "-b", "0.0.0.0:5000", "run:app"]
