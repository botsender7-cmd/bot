FROM python:3.11-slim

# python:3.11-slim does not ship tzdata, so any code that resolves the
# "local" timezone (tzlocal, APScheduler without an explicit timezone=,
# etc.) has no zoneinfo database to resolve against — behavior is
# undefined and can differ between environments. Installing tzdata and
# pinning TZ=UTC makes the container's notion of "local time" match what
# this codebase already assumes everywhere (all stored datetimes are
# naive UTC).
ENV TZ=UTC
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# On Render (Web Service type), Render sets RENDER_EXTERNAL_URL and PORT
# automatically — bot.py detects RENDER_EXTERNAL_URL and switches to
# webhook mode, binding 0.0.0.0:$PORT. Falls back to polling if no public
# URL env var is present (e.g. running locally).
EXPOSE 8080
CMD ["python", "bot.py"]
