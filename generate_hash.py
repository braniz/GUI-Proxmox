"""Erzeugt einen Passwort-Hash für ADMIN_PASSWORD_HASH."""
import getpass

from werkzeug.security import generate_password_hash

if __name__ == "__main__":
    pw = getpass.getpass("Passwort: ")
    if pw != getpass.getpass("Wiederholen: "):
        raise SystemExit("Passwörter stimmen nicht überein.")
    print(generate_password_hash(pw))
