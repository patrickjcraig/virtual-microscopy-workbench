"""Reproduce the supplied, explicitly synthetic microelectronics specimens."""
from pathlib import Path
import json


def primitive(id, name, material, center, size, shape="box", role="structure"):
    return dict(id=id, name=name, material=material, center_mm=center, size_mm=size, shape=shape, role=role)


def bga():
    objects = [
        primitive("mold", "Epoxy encapsulant", "epoxy", [4,4,0.46], [8,8,0.92]),
        primitive("die", "Silicon logic die", "silicon", [4,4,0.64], [4.2,4.2,0.32]),
        primitive("underfill", "Die attach / underfill", "epoxy", [4,4,0.86], [4.4,4.4,0.12]),
        primitive("substrate", "FR-4 package substrate", "fr4", [4,4,1.08], [8,8,0.32]),
        primitive("plane", "Copper ground plane", "copper", [4,4,1.06], [7.6,7.6,0.04]),
    ]
    for i in range(8):
        for j in range(8):
            x,y = round(0.85+0.9*i,3),round(0.85+0.9*j,3)
            objects.append(primitive(f"pad-{i}-{j}", f"Copper land {i+1}:{j+1}", "copper", [x,y,1.27], [.44,.44,.06], "cylinder"))
            objects.append(primitive(f"joint-{i}-{j}", f"Solder joint {i+1}:{j+1}", "solder", [x,y,1.55], [.52,.52,.52], "sphere"))
    for i in range(8):
        x=round(0.85+.9*i,3)
        objects.append(primitive(f"trace-{i}",f"Copper trace {i+1}","copper",[x,4,0.943],[.13,7.3,.046]))
    objects.extend([
        primitive("delamination", "Air-filled die-attach delamination", "air", [3.1,3.1,0.8175], [1.25,1.05,0.045], role="defect"),
        primitive("void-a", "Solder void A", "air", [2.65,4.45,1.55], [.28,.28,.28], "sphere", "defect"),
        primitive("void-b", "Solder void B", "air", [5.35,2.65,1.55], [.23,.23,.23], "sphere", "defect"),
        primitive("void-c", "Solder void C", "air", [6.25,6.25,1.55], [.19,.19,.19], "sphere", "defect"),
    ])
    return dict(schema_version=1, name="Flip-chip BGA / 64 joints", description="Synthetic 8 mm package with a silicon die, copper routing, FR-4 substrate and 64 solder joints. Seeded truth: one die-attach delamination and three solder voids. Not a measured device.", size_mm=[8,8,1.9], objects=objects)


def coupon():
    objects = [
        primitive("cap", "Epoxy package", "epoxy", [3,3,.35], [6,6,.7]),
        primitive("die", "Silicon power die", "silicon", [3,3,.43], [3.8,3.8,.34]),
        primitive("attach", "Solder die attach", "solder", [3,3,.65], [4,4,.1]),
        primitive("spreader", "Copper heat spreader", "copper", [3,3,.9], [5.8,5.8,.4]),
        primitive("carrier", "FR-4 carrier", "fr4", [3,3,1.25], [6,6,.3]),
        primitive("void", "Die-attach void", "air", [2.2,2.4,.65], [.7,.7,.06], "cylinder", "defect"),
        primitive("delam", "Corner delamination", "air", [4.2,4.2,.6075], [.9,.9,.045], role="defect"),
    ]
    return dict(schema_version=1,name="Power die / bonded copper",description="Synthetic planar power-electronics coupon with a silicon die, solder attach and copper heat spreader. An attach void and corner delamination provide controlled acoustic/X-ray comparisons.",size_mm=[6,6,1.5],objects=objects)


if __name__ == "__main__":
    root=Path(__file__).resolve().parents[1]/"examples"
    root.mkdir(exist_ok=True)
    for filename,twin in [("flip-chip-bga.json",bga()),("power-die.json",coupon())]:
        target=root/filename
        # This script refuses to replace a supplied or user-edited twin.
        with target.open("x",encoding="utf-8") as f:
            json.dump(twin,f,indent=2)
            f.write("\n")
