"""Pesos originales autorizados por Meta; token leído de forma privada."""
from getpass import getpass
from pathlib import Path

from huggingface_hub import snapshot_download

if __name__ == "__main__":
    token = getpass("Token Hugging Face con acceso aprobado a Llama: ")
    snapshot_download("meta-llama/Llama-3.2-1B-Instruct",
                      revision="9213176726f574b556790deb65791e0c5aa438b6",
                      local_dir=Path(".models/llama-base"), token=token)
    print("Pesos descargados en .models/llama-base. No publicarlos.")
