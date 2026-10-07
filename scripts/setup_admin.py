"""Bootstrap explícito de una cuenta privada; nunca imprime la contraseña."""
import argparse
import getpass
import json
import os
import secrets
from pathlib import Path

from argon2 import PasswordHasher


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--username", default="administrador")
    parser.add_argument("--generate", action="store_true")
    args = parser.parse_args()
    if args.file.exists():
        raise SystemExit("La cuenta ya existe; no se reemplaza automáticamente")
    if args.generate:
        password = secrets.token_urlsafe(24)
    else:
        password = getpass.getpass("Elige la contraseña del panel: ")
        if len(password) < 12 or password != getpass.getpass("Repite la contraseña: "):
            raise SystemExit("Debe coincidir y tener al menos12caracteres")
    args.file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(args.file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump({"username": args.username, "password_hash": PasswordHasher().hash(password)}, handle)
    if args.generate:
        note = args.file.parent / "admin-first-access.txt"
        descriptor = os.open(note, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write("Acceso privado inicial, no compartir ni publicar.\nUsuario: " + args.username +
                         "\nContraseña: " + password + "\n")
        print("Cuenta creada. Acceso inicial en", note)
    else:
        print("Cuenta creada con hash; contraseña no registrada en texto")


if __name__ == "__main__":
    main()
