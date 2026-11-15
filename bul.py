"""Model kodlarından Akakçe ürün linklerini bulur.

Kullanım: py bul.py aranacaklar.json
aranacaklar.json: [{"id": ..., "ad": ..., "ara": "65T8D", "kategori": "televizyon", "hedef": 44000}, ...]
Bulunanları urunler.json'a ekler (aynı id varsa atlar).
"""
import json
import re
import sys
import time
import urllib.parse
from pathlib import Path

from ortak import sayfa_getir

KLASOR = Path(__file__).parent
URUNLER = KLASOR / "urunler.json"


def sadele(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def bul(ara, kategori):
    html = sayfa_getir("https://www.akakce.com/arama/?q=" + urllib.parse.quote(ara))
    for yol in re.findall(r'href="(/[^"]*fiyati,\d+\.html)"', html):
        if yol.startswith("/" + kategori + "/") and sadele(ara) in sadele(yol):
            return "https://www.akakce.com" + yol
    return None


def main():
    aranacaklar = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8-sig"))
    urunler = json.loads(URUNLER.read_text(encoding="utf-8")) if URUNLER.exists() else []
    mevcut = {u["id"] for u in urunler}
    for a in aranacaklar:
        if a["id"] in mevcut:
            continue
        link = bul(a["ara"], a["kategori"])
        print(("OK   " if link else "YOK  ") + a["id"] + "  " + (link or ""))
        if link:
            urunler.append({"id": a["id"], "ad": a["ad"], "esya": a["esya"],
                            "hedef": a.get("hedef"), "akakce": link})
        time.sleep(2)
    URUNLER.write_text(json.dumps(urunler, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
