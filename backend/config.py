"""Shared paths and settings."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CACHE = DATA / "cache"
MODELS = ROOT / "models"
FRONTEND = ROOT / "frontend"

FIRST_SEASON = 2001  # last 25 years + current season

# Jolpica is the community successor of the Ergast API (same schema as the Kaggle dataset)
JOLPICA = "https://api.jolpi.ca/ergast/f1"
# Open-Meteo: ERA5 reanalysis (archive) and forecast endpoints, free, no API key
METEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
METEO_FORECAST = "https://api.open-meteo.com/v1/forecast"

for p in (DATA, CACHE, MODELS):
    p.mkdir(parents=True, exist_ok=True)
