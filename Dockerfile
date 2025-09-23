FROM python:3.12-slim

# Git installieren
RUN apt-get update && apt-get install -y \
    build-essential \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python Dependencies
COPY requirements.txt .
RUN pip install -r requirements.txt

# Jupyter installieren
RUN pip install jupyter notebook

# Port für Jupyter öffnen
EXPOSE 8888

# Daten-Ordner erstellen
RUN mkdir -p /app/data/raw /app/data/processed

CMD ["bash"]