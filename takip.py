"""urunler.json'daki ürünlerin Akakçe fiyatlarını çeker, kaydeder ve Telegram'a bildirim gönderir.

Her ürün Akakçe'nin son 6 aylık fiyat geçmişine göre değerlendirilir:
- "Normal fiyat": son 90 günün günlük fiyatlarının ortancası
- 🟢 Al:    hedefin altında, ya da son 3 ayın dibine yakın (%2) ve normalden en az %5 ucuz
- 🟡 Bekle: normalden en az %3 ucuz ama dipte değil
- ⚪ Normal: geri kalanlar

Anlık bildirim: ürün 🟢'ye geçince, hedefin altına inince veya %7+ ani düşüşte.
Her sabah 09:00'dan sonraki ilk çalışmada günlük özet gönderilir.
"""
import bisect
import csv
import json
import os
import random
import statistics
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from ortak import gecmisi_oku, sayfa_getir, teklifleri_oku

KLASOR = Path(__file__).parent
VERI = KLASOR / "veri"
FIYATLAR = VERI / "fiyatlar.csv"
DURUM = VERI / "durum.json"
TR = timezone(timedelta(hours=3))

ETIKETLI_SATICILAR = {"kapidakifirsat": "Kapıdaki Fırsat"}
OZEL_GUNLER = [("Efsane Cuma", date(2026, 11, 27))]
ANI_DUSUS_ESIGI = 0.93
OZET_SURUMU = 2  # özet biçimi değişince aynı gün yeniden gönderilsin diye
AYLAR = ["", "Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"]


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
    # Telegram 4096 karakter sınırı: uzun mesajı paragraf sınırlarından böl.
    parcalar, parca = [], ""
    for paragraf in mesaj.split("\n\n"):
        if parca and len(parca) + len(paragraf) > 3500:
            parcalar.append(parca)
            parca = ""
        parca = f"{parca}\n\n{paragraf}" if parca else paragraf
    parcalar.append(parca)
    for p in parcalar:
        veri = urllib.parse.urlencode({
            "chat_id": chat, "text": p, "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }).encode()
        urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", veri, timeout=30)


def istatistik(gecmis, fiyat, bugun):
    """Akakçe geçmişinden (değişim noktaları) son 180 günün günlük fiyat serisini kurup özetler."""
    noktalar = [(t, p) for t, p in gecmis if t < bugun] + [(bugun, fiyat)]
    tarihler = [t for t, _ in noktalar]
    gunluk = []
    for k in range(179, -1, -1):
        gun = bugun - timedelta(days=k)
        i = bisect.bisect_right(tarihler, gun) - 1
        if i >= 0:
            gunluk.append((gun, noktalar[i][1]))
    # Normalin %60'ının altı neredeyse her zaman Akakçe'de yanlış eşleşmiş/hatalı fiyattır.
    orta = statistics.median(p for _, p in gunluk)
    gunluk = [(g, p) for g, p in gunluk if p >= orta * 0.6] or [(bugun, fiyat)]
    # Dip: en düşük %5'lik dilim. Akakçe'de 1-2 gün görünen hatalı fiyatlar dibi bozmasın.
    son90 = sorted(p for _, p in gunluk[-90:])
    dip90 = son90[len(son90) // 20]
    sirali = sorted(gunluk, key=lambda x: x[1])
    dip180_gun, dip180 = sirali[len(sirali) // 20]
    return {"normal": statistics.median(son90), "dip90": dip90, "dip180": dip180,
            "dip180_ay": f"{AYLAR[dip180_gun.month]} {dip180_gun.year}", "gun": len(gunluk)}


def karar(u, fiyat, ist):
    """('al' | 'bekle' | 'normal', kısa açıklama)"""
    if u.get("hedef") and fiyat <= u["hedef"]:
        return "al", "Hedef fiyatın altında"
    if ist["gun"] < 30:
        return "normal", "Yeterli fiyat geçmişi yok"
    indirim = 1 - fiyat / ist["normal"]
    if fiyat <= ist["dip90"] * 1.02 and indirim >= 0.05:
        return "al", "Son 3 ayın en düşük fiyatı"
    if indirim >= 0.03:
        return "bekle", f"İndirimde ama 3 ay içinde {tl(ist['dip90'])} görmüştü"
    return "normal", ""


def fark_yazisi(fiyat, normal):
    oran = 1 - fiyat / normal
    if oran >= 0.01:
        return f"normali {tl(normal)} → <b>%{oran * 100:.0f} ucuz</b>"
    if oran <= -0.01:
        return f"normali {tl(normal)} → %{-oran * 100:.0f} pahalı"
    return f"normal fiyatında ({tl(normal)})"


def kart(u, d):
    """Bir ürün için okunaklı, çok satırlı açıklama."""
    s, ist = d["son"], d["ist"]
    _, aciklama = karar(u, s["fiyat"], ist)
    satirlar = [f"<b>{u['ad']}</b> ({u['esya']})",
                f"<a href=\"{s['link']}\">{tl(s['fiyat'])}</a> · {s['satici']}"]
    if ist["gun"] >= 30:
        satirlar.append(fark_yazisi(s["fiyat"], ist["normal"]))
    if aciklama:
        satirlar.append(aciklama)
    if ist["gun"] >= 30 and ist["dip180"] < s["fiyat"] * 0.97:
        satirlar.append(f"6 ayın en düşüğü: {tl(ist['dip180'])} ({ist['dip180_ay']})")
    if etiket(s["satici"]) and s.get("guvenli"):
        satirlar.append(f"⚠️ {etiket(s['satici'])}. Diğer satıcılarda en ucuz: {tl(s['guvenli'])}")
    if u.get("hedef"):
        satirlar.append(f"🎯 Hedef: {tl(u['hedef'])}")
    return "\n".join(satirlar)


def kontrol_et(u, teklifler, gecmis, d, simdi):
    """Ürün durumunu günceller; anlık bildirim gerekiyorsa mesaj döndürür."""
    en_ucuz = teklifler[0]
    guvenli = next((t for t in teklifler if not etiket(t["satici"])), None)
    fiyat = en_ucuz["fiyat"]
    ist = istatistik(gecmis, fiyat, simdi.date())
    kod, _ = karar(u, fiyat, ist)

    sebepler = []
    if kod == "al" and d.get("karar") not in (None, "al"):
        sebepler.append("🟢 <b>Alınabilir seviyeye indi</b>")
    if "son_fiyat" in d and fiyat <= d["son_fiyat"] * ANI_DUSUS_ESIGI:
        sebepler.append(f"⚡ <b>Ani düşüş:</b> {tl(d['son_fiyat'])} → {tl(fiyat)}")

    # Aynı fiyat için tekrar tekrar bildirim gönderme; fiyat %3 yükselirse sıfırla.
    son_bildirim = d.get("son_bildirim")
    if son_bildirim and fiyat > son_bildirim * 1.03:
        d.pop("son_bildirim")
        son_bildirim = None

    d.setdefault("ilk_kayit", simdi.isoformat())
    d["en_dusuk"] = min(fiyat, d.get("en_dusuk", fiyat))
    d["son_fiyat"] = fiyat
    d["karar"] = kod
    d["ist"] = ist
    d["son"] = {"fiyat": fiyat, "satici": en_ucuz["satici"], "link": en_ucuz["link"],
                "guvenli": guvenli["fiyat"] if guvenli else None}

    if sebepler and (son_bildirim is None or fiyat < son_bildirim * 0.995):
        d["son_bildirim"] = fiyat
        return "\n".join(sebepler) + "\n\n" + kart(u, d)
    return None


def gunluk_ozet(urunler, durum, bugun):
    gruplar = {"al": [], "bekle": [], "normal": []}
    for u in urunler:
        d = durum["urunler"].get(u["id"], {})
        if "ist" not in d:
            continue
        ind = 1 - d["son"]["fiyat"] / d["ist"]["normal"]
        gruplar[d["karar"]].append((ind, u, d))
    for g in gruplar.values():
        g.sort(key=lambda x: -x[0])

    bolumler = [f"☀️ <b>Fiyat özeti · {bugun.day} {AYLAR[bugun.month]}</b>"]
    for ad, gun in OZEL_GUNLER:
        kalan = (gun - bugun).days
        if 0 < kalan <= 30:
            bolumler[0] += f"\n⏳ {ad}'ya {kalan} gün var. 🟢 olmayanlar için acele etme."
        elif kalan == 0:
            bolumler[0] += f"\n🔥 Bugün {ad}!"

    if gruplar["al"]:
        bolumler.append("🟢 <b>ALINABİLİR</b>")
        bolumler += [kart(u, d) for _, u, d in gruplar["al"]]
    else:
        bolumler.append("🟢 Şu an gerçekten indirimde olan ürün yok.")

    if gruplar["bekle"]:
        satirlar = ["🟡 <b>İNDİRİMDE AMA BEKLE</b>"]
        for ind, u, d in gruplar["bekle"]:
            satirlar.append(f"• <a href=\"{d['son']['link']}\">{u['ad']}</a>: {tl(d['son']['fiyat'])}"
                            f" (%{ind * 100:.0f} ucuz, 3 ayın dibi {tl(d['ist']['dip90'])})")
        bolumler.append("\n".join(satirlar))

    if gruplar["normal"]:
        sayilar = {}
        for _, u, _ in gruplar["normal"]:
            sayilar[u["esya"]] = sayilar.get(u["esya"], 0) + 1
        ozet = ", ".join(f"{esya} {n}" for esya, n in sayilar.items())
        bolumler.append(f"⚪ <b>Normal fiyatında ({len(gruplar['normal'])} ürün):</b> {ozet}")
    return "\n\n".join(bolumler)


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
                html = sayfa_getir(u["akakce"])
                teklifler = teklifleri_oku(html)
                gecmis = gecmisi_oku(html)
            except Exception as e:
                hatalar.append(f"{u['id']}: {e}")
                continue
            if not teklifler:
                hatalar.append(f"{u['id']}: teklif bulunamadı")
                continue
            d = durum["urunler"].setdefault(u["id"], {})
            mesaj = kontrol_et(u, teklifler, gecmis, d, simdi)
            yazici.writerow([simdi.isoformat(), u["id"], teklifler[0]["fiyat"], teklifler[0]["satici"],
                             len(teklifler), d["son"]["guvenli"] or "", teklifler[0]["link"]])
            print(f"{u['id']}: {tl(teklifler[0]['fiyat'])} ({teklifler[0]['satici']}) [{d['karar']}]")
            if mesaj:
                telegram(mesaj)
            time.sleep(1 + random.random())

    bugun = simdi.date()
    if simdi.hour >= 9 and (durum.get("son_ozet") != bugun.isoformat()
                            or durum.get("ozet_surumu") != OZET_SURUMU):
        telegram(gunluk_ozet(urunler, durum, bugun))
        durum["son_ozet"] = bugun.isoformat()
        durum["ozet_surumu"] = OZET_SURUMU

    if hatalar:
        print("HATALAR:\n" + "\n".join(hatalar), file=sys.stderr)
        # Çoğu ürün okunamadıysa site engellemiş olabilir; günde bir kez haber ver.
        if len(hatalar) > len(urunler) / 2 and durum.get("son_hata_bildirimi") != bugun.isoformat():
            telegram(f"⚠️ Fiyat takibi: {len(hatalar)}/{len(urunler)} ürün okunamadı. Site engellemiş olabilir.")
            durum["son_hata_bildirimi"] = bugun.isoformat()

    DURUM.write_text(json.dumps(durum, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
