FROM python:3.12-slim

# System packages (git, build tools, htop for monitoring, curl+gnupg for the step below)
RUN apt-get update && apt-get install -y \
    build-essential \
    git \
    htop \
    curl \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# GitHub CLI — issue bodies and comments are transferred with it, so it belongs in the
# image rather than in whoever remembers to install it. Not in Debian stable, hence the
# vendor repository.
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
      > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update && apt-get install -y gh \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python Dependencies
COPY requirements.txt .
RUN pip install -r requirements.txt

# Daten-Ordner erstellen
RUN mkdir -p /app/data/raw /app/data/processed

# Bash als interaktive Login-Shell setzen
CMD ["/bin/bash", "-l"]