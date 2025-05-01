# Utiliser une image Python légère
FROM python:3.9-slim

# Définir le répertoire de travail
WORKDIR /app

# Copier les fichiers du projet
COPY . .

# Installer les dépendances
RUN pip install --no-cache-dir ccxt tweepy schedule python-dotenv

# Commande pour exécuter le bot
CMD ["python", "main.py"]