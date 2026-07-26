# Roman Job Radar — dashboard + pipeline image.
#
# Not required for normal use (see README.md — everything runs directly via
# venv today, and Windows Task Scheduler is the primary scheduling method).
# This exists as an alternative deployment path, e.g. for running the
# dashboard on a home server or NAS.
FROM python:3.12-slim

WORKDIR /app

# build-essential covers the rare case a dependency needs to compile from
# source instead of using a prebuilt wheel for this platform.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Pre-download the sentence-transformer model at build time so the
# container never needs network access to score jobs at runtime — this is
# a real, tested requirement (see src/matching/semantic_matcher.py: without
# forcing a local-cache-only load, this model tries to reach Hugging Face
# Hub on every fresh process even when nothing has changed).
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

EXPOSE 8501

CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501"]
