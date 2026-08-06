"""Config minimale pour que les modules wastream soient importables en test,
sans avoir besoin d'un .env complet (Settings() est instancie au chargement
du module wastream.config.settings)."""
import os

os.environ.setdefault("SECRET_KEY", "0" * 32)
