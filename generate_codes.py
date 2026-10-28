"""Génère des lots de codes d'accès Premium (même secret que le serveur).

Utilisation :
    ACCESS_SECRET=<le secret défini sur Render> python generate_codes.py --semaine 50 --mois 50 --out codes.csv

Chaque code expire à une date FIXE (aujourd'hui + durée de la formule) : génère les codes peu avant de les vendre.
"""
import argparse
import csv
import os
import sys

if not os.getenv("ACCESS_SECRET"):
    sys.exit("ACCESS_SECRET manquant : utilise exactement le même secret que sur Render.")

import access  # noqa: E402  (lit ACCESS_SECRET à l'import)

PLANS = {"semaine": 7, "mois": 30}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--semaine", type=int, default=0)
    ap.add_argument("--mois", type=int, default=0)
    ap.add_argument("--out", default="codes.csv")
    args = ap.parse_args()

    rows, seen = [], set()
    for plan, count in (("semaine", args.semaine), ("mois", args.mois)):
        while sum(1 for r in rows if r[1] == plan) < count:
            code, expires = access.make_code(PLANS[plan])
            if code in seen:
                continue
            seen.add(code)
            assert access.check_code(code) == expires
            rows.append((len(rows) + 1, plan, code, expires.isoformat(), "", "", ""))

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["n", "formule", "code", "expire_le", "client", "montant_fcfa", "date_vente"])
        w.writerows(rows)
    print(f"{len(rows)} codes écrits dans {args.out}")


if __name__ == "__main__":
    main()
