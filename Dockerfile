FROM python:3.12-slim

# fonts-dejavu-core provides DejaVuSans.ttf, which viwoods/exporter.py looks
# for at /usr/share/fonts so PDF exports can embed a Unicode font.
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY companion.py .
COPY viwoods ./viwoods

# Config, sync state, OCR cache and downloaded page scans (contains your
# Viwoods token) - mount a volume here so they survive container recreation.
ENV VIWOODS_DATA_DIR=/data
RUN mkdir -p /data /app/exports

RUN useradd --create-home --uid 1000 viwoods \
    && chown -R viwoods:viwoods /app /data
USER viwoods

EXPOSE 8765

# Runs the dashboard bound to all interfaces so the port mapping below works;
# companion.py itself is untouched and still defaults to loopback-only when
# run outside Docker. Override the command (e.g. to
# "python companion.py daemon --interval 30") to run headless without the
# dashboard.
CMD ["uvicorn", "viwoods.server:app", "--host", "0.0.0.0", "--port", "8765"]
