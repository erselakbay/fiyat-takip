"""urunler.json'daki ürünlerin Akakçe fiyatlarını çeker, kaydeder ve Telegram'a bildirim gönderir.

Bildirim durumları:
- Fiyat hedefin altında (hedef tanımlıysa)
- Kendi kayıtlarımızdaki en düşük fiyatın %2+ altına indi (en az 1 günlük veri varsa)
- Bir önceki ölçüme göre %7+ ani düşüş
Her sabah 09:00'dan sonraki ilk çalışmada günlük özet gönderilir.
"""
import csv
import json
import os
import random
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ortak import sayfa_getir, teklifleri_oku

KLASOR = Path(__file__).parent
VERI = KLASOR / "veri"
FIYATLAR = VERI / "fiyatlar.csv"
DURUM = VERI / "durum.json"
TR = timezone(timedelta(hours=3))

ETIKETLI_SATICILAR = {"kapidakifirsat": "Kapıdaki Fırsat"}
DIP_ESIGI = 0.98
ANI_DUSUS_ESIGI = 0.93
DIP_ICIN_GEREKEN_SURE = timedelta(days=1)


def tl(x):
    return f"{x:,.0f}".replace(",", ".") + " TL"


def etiket(satici):
    for anahtar, ad in ETIKETLI_SATICILAR.items():
        if anahtar in satici.lower():
            return ad
    return None


def telegram(mesaj):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("[telegram yok]\n" + mesaj + "\n")
        return
    veri = urllib.parse.urlencode({
        "chat_id": chat, "text": mesaj, "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", veri, timeout=30)


def satir(u, en_ucuz, guvenli):
    s = f"<b>{u['ad']}</b>: <a href=\"{en_ucuz['link']}\">{tl(en_ucuz['fiyat'])}</a> ({en_ucuz['satici']})"
    if etiket(en_ucuz["satici"]) and guvenli:
        s += f"\n   ⚠️ {etiket(en_ucuz['satici'])}. Diğer satıcılarda en ucuz: {tl(guvenli['fiyat'])} ({guvenli['satici']})"
    if u.get("hedef"):
        s += f"\n   Hedef: {tl(u['hedef'])}"
    return s


def kontrol_et(u, teklifler, d, simdi):
    """Bildirim gerekiyorsa mesaj döndürür, ürünün durumunu günceller."""
    en_ucuz = teklifler[0]
    guvenli = next((t for t in teklifler if not etiket(t["satici"])), None)
    fiyat = en_ucuz["fiyat"]
    sebepler = []

    if u.get("hedef") and fiyat <= u["hedef"]:
        sebepler.append("🎯 Hedef fiyatın altında")
    ilk = datetime.fromisoformat(d["ilk_kayit"]) if "ilk_kayit" in d else simdi
    if "en_dusuk" in d and simdi - ilk >= DIP_ICIN_GEREKEN_SURE and fiyat < d["en_dusuk"] * DIP_ESIGI:
        sebepler.append(f"📉 Takip başladığından beri en düşük (önceki dip {tl(d['en_dusuk'])})")
    if "son_fiyat" in d and fiyat <= d["son_fiyat"] * ANI_DUSUS_ESIGI:
        sebepler.append(f"⚡ Ani düşüş: {tl(d['son_fiyat'])} → {tl(fiyat)}")

    # Aynı fiyat için tekrar tekrar bildirim gönderme; fiyat %3 yükselirse sıfırla.
    son_bildirim = d.get("son_bildirim")
    if son_bildirim and fiyat > son_bildirim * 1.03:
        d.pop("son_bildirim")
        son_bildirim = None
    mesaj = None
    if sebepler and (son_bildirim is None or fiyat < son_bildirim * 0.995):
        mesaj = "\n".join(sebepler) + "\n" + satir(u, en_ucuz, guvenli)
        d["son_bildirim"] = fiyat

    d.setdefault("ilk_kayit", simdi.isoformat())
    d["en_dusuk"] = min(fiyat, d.get("en_dusuk", fiyat))
    d["son_fiyat"] = fiyat
    d["son"] = {"fiyat": fiyat, "satici": en_ucuz["satici"], "link": en_ucuz["link"],
                "guvenli": guvenli["fiyat"] if guvenli else None}
    return mesaj


def gunluk_ozet(urunler, durum):
    satirlar = ["☀️ <b>Günlük fiyat özeti</b>"]
    esya = None
    for u in urunler:
        d = durum["urunler"].get(u["id"], {})
        if "son" not in d:
            continue
        if u["esya"] != esya:
            esya = u["esya"]
            satirlar.append(f"\n<b>{esya}</b>")
        s = d["son"]
        isaret = "🎯 " if u.get("hedef") and s["fiyat"] <= u["hedef"] else ""
        ek = f" / hedef {tl(u['hedef'])}" if u.get("hedef") else ""
        satirlar.append(f"{isaret}{u['ad']}: <a href=\"{s['link']}\">{tl(s['fiyat'])}</a>"
                        f" (dip {tl(d['en_dusuk'])}{ek})")
    return "\n".join(satirlar)


def main():
    urunler = json.loads((KLASOR / "urunler.json").read_text(encoding="utf-8"))
    VERI.mkdir(exist_ok=True)
    durum = json.loads(DURUM.read_text(encoding="utf-8")) if DURUM.exists() else {}
    durum.setdefault("urunler", {})
    simdi = datetime.now(TR).replace(microsecond=0)

    yeni_dosya = not FIYATLAR.exists()
    hatalar = []
    with FIYATLAR.open("a", newline="", encoding="utf-8") as f:
        yazici = csv.writer(f)
        if yeni_dosya:
            yazici.writerow(["zaman", "id", "fiyat", "satici", "teklif_sayisi", "guvenli_fiyat", "link"])
        for u in urunler:
            try:
                teklifler = teklifleri_oku(sayfa_getir(u["akakce"]))
            except Exception as e:
                hatalar.append(f"{u['id']}: {e}")
                continue
            if not teklifler:
                hatalar.append(f"{u['id']}: teklif bulunamadı")
                continue
            d = durum["urunler"].setdefault(u["id"], {})
            mesaj = kontrol_et(u, teklifler, d, simdi)
            yazici.writerow([simdi.isoformat(), u["id"], teklifler[0]["fiyat"], teklifler[0]["satici"],
                             len(teklifler), d["son"]["guvenli"] or "", teklifler[0]["link"]])
            print(f"{u['id']}: {tl(teklifler[0]['fiyat'])} ({teklifler[0]['satici']})")
            if mesaj:
                telegram(mesaj)
            time.sleep(1 + random.random())

    bugun = simdi.date().isoformat()
    if simdi.hour >= 9 and durum.get("son_ozet") != bugun:
        telegram(gunluk_ozet(urunler, durum))
        durum["son_ozet"] = bugun

    if hatalar:
        print("HATALAR:\n" + "\n".join(hatalar), file=sys.stderr)
        # Çoğu ürün okunamadıysa site engellemiş olabilir; günde bir kez haber ver.
        if len(hatalar) > len(urunler) / 2 and durum.get("son_hata_bildirimi") != bugun:
            telegram(f"⚠️ Fiyat takibi: {len(hatalar)}/{len(urunler)} ürün okunamadı. Site engellemiş olabilir.")
            durum["son_hata_bildirimi"] = bugun

    DURUM.write_text(json.dumps(durum, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
