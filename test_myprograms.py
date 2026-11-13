"""Kanalval för Mina program (inga nätverksanrop)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tv_hub", "app"))
from hub import myprograms  # noqa: E402

fails = 0


def check(name, cond):
    global fails
    print(("  ok   " if cond else "  FEL  ") + name)
    fails += 0 if cond else 1


sc = myprograms.channel_score
for name, target in (("TV4", "TV4"), ("SE: TV4 HD", "TV4"), ("SE| TV4 FHD", "TV4"), ("TV 4", "TV4"), ("SVT1", "SVT1"),
                     ("SE: SVT 1 HD", "SVT1"), ("SVT2 HD", "SVT2")):
    check(f"hittar {name} som {target}", sc(name, target) is not None)
for name, target in (("CCTV4 中文国际", "TV4"), ("CCTV 4", "TV4"), ("SE: TV4 Film", "TV4"), ("TV4 Sport", "TV4"),
                     ("SVT1 Plus", "SVT1"), ("SE: SVT Barn", "SVT1"), ("TV4 Fakta", "TV4"), ("DE: ZDF", "SVT1")):
    check(f"hittar inte {name} som {target}", sc(name, target) is None)
check("svenskt prefix och HD föredras", sc("SE: TV4 HD", "TV4") > sc("TV4", "TV4"))
print("ALLT OK" if not fails else f"{fails} FEL")
sys.exit(1 if fails else 0)
