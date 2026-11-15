"""Akakçe'den sayfa çekme ve fiyat okuma için ortak yardımcılar."""
import json
import re
import time
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


def sayfa_getir(url, deneme=3):
    istek = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "tr-TR,tr;q=0.9",
    })
    for i in range(deneme):
        try:
            with urllib.request.urlopen(istek, timeout=30) as yanit:
                return yanit.read().decode("utf-8", errors="replace")
        except Exception:
            if i == deneme - 1:
                raise
            time.sleep(5 * (i + 1))


def teklifleri_oku(html):
    """Akakçe ürün sayfasındaki schema.org verisinden satıcı tekliflerini döndürür."""
    m = re.search(r'application/ld\+json">(.*?)</script>', html, re.S)
    if not m:
        return None
    veri = json.loads(m.group(1))
    teklifler = []
    for t in veri.get("offers", {}).get("offers", []):
        if "NewCondition" not in t.get("itemCondition", "NewCondition"):
            continue  # teşhir / yenilenmiş / ikinci el istemiyoruz
        if "InStock" not in t.get("availability", "InStock"):
            continue
        try:
            fiyat = float(t["price"])
        except (KeyError, ValueError):
            continue
        teklifler.append({
            "fiyat": fiyat,
            "satici": t.get("seller", {}).get("name", "?"),
            "link": t.get("url", ""),
        })
    teklifler.sort(key=lambda t: t["fiyat"])
    return teklifler
