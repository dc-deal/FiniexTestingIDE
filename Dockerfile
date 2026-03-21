FROM python:3.12-slim

# System-Pakete installieren (Git, Build-Tools und htop für Monitoring)
RUN apt-get update && apt-get install -y \
    build-essential \
    git \
    htop \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python Dependencies
COPY requirements.txt .
RUN pip install -r requirements.txt

# Daten-Ordner erstellen
RUN mkdir -p /app/data/raw /app/data/processed

# Bash als interaktive Login-Shell setzen
CMD ["/bin/bash", "-l"]