# Basis-Image: Python 3.12 (gleiche Version wie dein System)
FROM python:3.12-slim

# Arbeitsverzeichnis im Container
WORKDIR /app

# Systemabhängigkeiten für Data Processing
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Python Dependencies
COPY requirements.txt .
RUN pip install -r requirements.txt

# Jupyter für interaktive Entwicklung
RUN pip install jupyter notebook

# Port für Jupyter öffnen
EXPOSE 8888

# Daten-Ordner erstellen
RUN mkdir -p /app/data/raw /app/data/processed

# Startkommando
CMD ["bash"]