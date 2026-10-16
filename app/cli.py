"""Outils en ligne de commande (utilisés par install.sh).

  python -m app.cli useradd <nom> --source linux|local --group admin|user|display [--label X]
  python -m app.cli passwd <nom>            (mot de passe Centrol lu sur stdin)
  python -m app.cli verify-linux <nom>      (mot de passe Linux lu sur stdin)
  python -m app.cli list
"""
import argparse
import sys

from . import accounts as A
from .store import Store


def main():
    ap = argparse.ArgumentParser(prog="centrol")
    sp = ap.add_subparsers(dest="cmd", required=True)
    a = sp.add_parser("useradd")
    a.add_argument("name")
    a.add_argument("--source", choices=["linux", "local"], default="linux")
    a.add_argument("--group", default="user")
    a.add_argument("--label", default="")
    sp.add_parser("passwd").add_argument("name")
    sp.add_parser("verify-linux").add_argument("name")
    sp.add_parser("list")
    args = ap.parse_args()
    st = Store()

    if args.cmd == "list":
        for n, u in sorted(st.users.items()):
            print(f"{n:24} {u['source']:6} {u['group']}")
        return
    if args.cmd == "verify-linux":
        pw = sys.stdin.readline().rstrip("\n")
        if not A.linux_available():
            sys.exit("Fichier /etc/shadow de l'hôte illisible par Centrol")
        sys.exit(0 if A.verify_linux(args.name, pw) else "Mot de passe Linux incorrect")
    if args.cmd == "passwd":
        pw = sys.stdin.readline().rstrip("\n")
        if len(pw) < 8:
            sys.exit("8 caractères minimum")
        u = st.users.setdefault(args.name, {"group": "user", "moderates": [], "linux": None, "totp": None,
                                            "disabled": False})
        u.update(source="local", hash=A.hash_pw(pw))
        st.save_users()
        print(f"Mot de passe Centrol de « {args.name} » enregistré.")
        return
    if args.cmd == "useradd":
        if not st.group(args.group):
            sys.exit(f"Groupe inconnu : {args.group}")
        u = st.users.get(args.name, {"moderates": [], "totp": None, "disabled": False})
        u.update(source=args.source, group=args.group, label=args.label or u.get("label", ""),
                 linux=args.name if args.source == "linux" else u.get("linux"))
        if args.source == "linux":
            u.pop("hash", None)
        st.users[args.name] = u
        st.save_users()
        print(f"Compte « {args.name} » ({args.source}, groupe {args.group}) enregistré.")


if __name__ == "__main__":
    main()
