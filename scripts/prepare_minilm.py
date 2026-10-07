"""Descargar una vez la caché pública de recuperación y usarla después sin red."""
from pathlib import Path

from coffee_house.retrieval.search import MiniLMEncoder

if __name__ == "__main__":
    MiniLMEncoder(Path(".models/minilm"),local_files_only=False)
    print("MiniLM preparado en .models/minilm")
