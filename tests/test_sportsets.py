"""Kanalmatchning för Golf och Ridsport (inga nätverksanrop)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tv_hub", "app"))
from hub import sportsets  # noqa: E402

fails = 0


def check(name, cond):
    global fails
    print(("  ok   " if cond else "  FEL  ") + name)
    fails += 0 if cond else 1


def ded(set_name, name):
    cfg = sportsets.SETS[set_name]
    return sportsets._matches(name, cfg["dedicated"], cfg["dedicated_exclude"])


def car(set_name, name):
    return sportsets._matches(name, sportsets.SETS[set_name]["carriers"], [])


for n in ("V Sport Golf", "SE: Viaplay Golf HD", "Golf Channel", "Sky Sports Golf", "PGA Tour Live", "GOLF TV"):
    check(f"golf: {n}", ded("Golf", n))
for n in ("Minigolf TV", "Disc Golf Network", "VW Golf Shop", "SVT1", "Eurosport 1", "Sport Nyheter"):
    check(f"inte golf: {n}", not ded("Golf", n))
for n in ("Horse & Country", "H&C", "FEI TV", "Equestrian TV", "SE: Ridsport"):
    check(f"ridsport: {n}", ded("Ridsport", n))
for n in ("Sky Sports Racing", "V Sport Vinnare", "Trav TV", "Galopp Live", "V75 Live", "TV4"):
    check(f"inte ridsport: {n}", not ded("Ridsport", n))
for n in ("Eurosport 1", "Eurosport 2 HD", "SE: V Sport Premium", "TV4 Sportkanalen", "SVT1 HD", "SVT 2"):
    check(f"sänder golf bland annat: {n}", car("Golf", n))
for n in ("TV4 Sportkanalen", "TV4", "SVT2", "Eurosport 2"):
    check(f"sänder ridsport bland annat: {n}", car("Ridsport", n))
print("ALLT OK" if not fails else f"{fails} FEL")
sys.exit(1 if fails else 0)
