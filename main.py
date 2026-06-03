# main.py — Aplikacja-alertowa v2
# Nowe: podział na 2 grupy klientów (manualni vs auto-sterowani)
# - Manualni dostają stare wiadomości ("zalecamy wyłączenie")
# - Auto-sterowani dostają nowe wiadomości ("ograniczyliśmy produkcję")

import os
import time
import json
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
import psycopg2
from twilio.rest import Client
from dotenv import load_dotenv

load_dotenv()

# --- E-mail / SendGrid ---
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
EMAIL_OD = os.getenv("EMAIL_OD")  # np. alert@techion.com.pl

# Lista 1: STARZY klienci (manualnie sterują farmą — info "zalecamy wyłączenie")
EMAIL_DO = [a.strip() for a in (os.getenv("EMAIL_DO") or "").split(",") if a.strip()]

# Lista 2: NOWI klienci (my sterujemy farmą automatycznie — info "ograniczyliśmy produkcję")
EMAIL_DO_AUTO = [a.strip() for a in (os.getenv("EMAIL_DO_AUTO") or "").split(",") if a.strip()]

# --- WhatsApp (Twilio) ---
TWILIO_SID = os.getenv("TWILIO_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_FROM = os.getenv("TWILIO_WHATSAPP_FROM")

# Lista 1: STARZY klienci (manualne sterowanie)
WHATSAPP_NUMERY = [n.strip() for n in (os.getenv("WHATSAPP_NUMERY") or "").split(",") if n.strip()]
TEMPLATE_SID_PONIZEJ = os.getenv("TEMPLATE_SID_PONIZEJ")   # "Zalecamy wyłączenie"
TEMPLATE_SID_POWYZEJ = os.getenv("TEMPLATE_SID_POWYZEJ")   # "Zalecamy włączenie"

# Lista 2: NOWI klienci (automatyczne sterowanie)
WHATSAPP_NUMERY_AUTO = [n.strip() for n in (os.getenv("WHATSAPP_NUMERY_AUTO") or "").split(",") if n.strip()]
TEMPLATE_SID_AUTO_WYL = os.getenv("TEMPLATE_SID_AUTO_WYL")   # "Ograniczyliśmy produkcję"
TEMPLATE_SID_AUTO_ZAL = os.getenv("TEMPLATE_SID_AUTO_ZAL")   # "Przywróciliśmy normalną pracę"

# --- Railway DB ---
DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()

client = Client(TWILIO_SID, TWILIO_AUTH_TOKEN)

# -------------------- BAZA: stan --------------------
def zapisz_stan(czy_niska):
    print(f"💾 Zapisuję stan: {czy_niska}")
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute("UPDATE stan_alertu SET czy_niska = %s WHERE id = 1", (czy_niska,))
        conn.commit()
        cur.close()
        conn.close()
        print("✅ Zapisano stan do bazy.")
    except Exception as e:
        print(f"❌ Błąd zapisu stanu: {e}")
        traceback.print_exc()

def zapisz_zadanie_fs(wartosc):
    """Zapisuje zadanie dla lokalnego pollera FusionSolar (0=pełna moc, 6=ograniczenie)."""
    print(f"⚡ Zapisuję zadanie FusionSolar: {wartosc}")
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute("UPDATE stan_alertu SET fs_zadanie = %s WHERE id = 1", (wartosc,))
        conn.commit()
        cur.close()
        conn.close()
        print(f"✅ Zadanie FusionSolar ({wartosc}) zapisane do bazy.")
    except Exception as e:
        print(f"❌ Błąd zapisu zadania FusionSolar: {e}")
        traceback.print_exc()

def wczytaj_stan():
    print("📥 Wczytywanie stanu z bazy...")
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute("SELECT czy_niska FROM stan_alertu WHERE id = 1")
        wynik = cur.fetchone()
        cur.close()
        conn.close()
        if wynik:
            print(f"✅ Wczytany stan: {wynik[0]}")
            return wynik[0]
        else:
            print("⚠️ Brak rekordu o id=1 w tabeli.")
            return None
    except Exception as e:
        print(f"❌ Błąd odczytu stanu: {e}")
        traceback.print_exc()
        return None

# -------------------- E-MAIL przez SendGrid --------------------
def wyslij_maila(temat, tresc, odbiorcy):
    """Wyślij mail do podanej listy odbiorców. Każdy w osobnej personalizacji (nikt się nie widzi)."""
    if not odbiorcy:
        print(f"⚠️ Brak odbiorców dla maila '{temat}' — pomijam")
        return
    if not SENDGRID_API_KEY or not EMAIL_OD:
        print("❌ Brak SENDGRID_API_KEY lub EMAIL_OD – nie wyślę e-maila.")
        return

    personalizations = [{"to": [{"email": addr}]} for addr in odbiorcy]
    payload = {
        "personalizations": personalizations,
        "from": {"email": EMAIL_OD, "name": "Alert"},
        "reply_to": {"email": EMAIL_OD},
        "subject": temat,
        "content": [{"type": "text/plain", "value": tresc}]
    }
    try:
        r = requests.post(
            "https://api.sendgrid.com/v3/mail/send",
            headers={
                "Authorization": f"Bearer {SENDGRID_API_KEY}",
                "Content-Type": "application/json"
            },
            data=json.dumps(payload),
            timeout=(5, 15)
        )
        if 200 <= r.status_code < 300:
            print(f"✅ E-mail '{temat}' wysłany do {len(odbiorcy)} odbiorców.")
        else:
            print(f"❌ SendGrid {r.status_code}: {r.text}")
    except Exception as e:
        print(f"❌ Błąd HTTP przy wysyłce e-maila: {e}")
        traceback.print_exc()

# -------------------- WhatsApp --------------------
def wyslij_whatsapp(content_sid, numery):
    """Wyślij content_sid do podanej listy numerów."""
    if not numery:
        print(f"⚠️ Brak numerów dla SID {content_sid[:8] if content_sid else 'None'}... — pomijam")
        return
    if not content_sid:
        print("⚠️ Brak content_sid — pomijam wysyłkę WhatsApp")
        return
    for numer in numery:
        try:
            client.messages.create(
                to=numer,
                from_=TWILIO_WHATSAPP_FROM,
                content_sid=content_sid
            )
            print(f"📲 WhatsApp ({content_sid[:8]}...) wysłany do: {numer}")
        except Exception as e:
            print(f"❌ Błąd WhatsApp do {numer}: {e}")
            traceback.print_exc()

# -------------------- Logi --------------------
def zapisz_log_alertu(typ, cena, czas):
    print(f"📝 Logowanie alertu: {typ}, cena: {cena}, czas: {czas}")
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO log_alertow (data_wyslania, typ_alertu, cena, okres_czasowy)
            VALUES (NOW(), %s, %s, %s)
        """, (typ, cena, czas))
        conn.commit()
        cur.close()
        conn.close()
        print("✅ Zapisano log alertu do bazy.")
    except Exception as e:
        print(f"❌ Błąd zapisu logu: {e}")
        traceback.print_exc()

# -------------------- Logika --------------------
def sprawdz_ceny():
    global poprzednia_cena_niska
    dzis = datetime.now().strftime('%Y-%m-%d')
    url = (
        "https://apimpdv2-bmgdhhajexe8aade.a01.azurefd.net/api/price-fcst"
        f"?$filter=business_date eq '{dzis}'"
        "&$orderby=dtime asc"
    )
    try:
        response = requests.get(url, timeout=(5, 12))
        response.raise_for_status()
        dane = response.json().get("value", [])
    except Exception as e:
        print(f"❌ Błąd pobierania danych: {e}")
        traceback.print_exc()
        return

    if not dane:
        print("⚠️ Brak danych z API.")
        return

    czas_lokalny = datetime.now(ZoneInfo("Europe/Warsaw"))
    print(f"\n[{czas_lokalny.strftime('%Y-%m-%d %H:%M:%S')}] Dane PSE (prognoza):")
    for rekord in dane:
        cena_p = rekord.get("cen_fcst", 9999)
        czas_p = rekord.get("period", rekord.get("dtime", "brak"))
        print(f"Godzina: {czas_p} | Cena: {cena_p} zł")

    ostatni_rekord = dane[-1]
    cena = ostatni_rekord.get("cen_fcst", 9999)
    czas = ostatni_rekord.get("period", ostatni_rekord.get("dtime", "brak"))
    print(f"\n➡️ Ostatnia cena: {cena} zł o {czas}")

    if cena <= 30:
        if poprzednia_cena_niska is not True:
            print("⚠️ Cena niska — wysyłamy powiadomienia do OBU grup klientów...")

            # === GRUPA 1: STARZY KLIENCI (manualne sterowanie) ===
            wyslij_maila(
                "UWAGA Możliwe ujemne wartości",
                "UWAGA! Możliwe ujemne wartości rozliczeń za energię elektryczną. Zalecamy wyłączenie farmy.",
                EMAIL_DO
            )
            wyslij_whatsapp(TEMPLATE_SID_PONIZEJ, WHATSAPP_NUMERY)

            # === GRUPA 2: NOWI KLIENCI (auto-sterowani, my wyłączyliśmy) ===
            wyslij_maila(
                "Ograniczyliśmy produkcję Państwa farmy PV",
                "UWAGA! Ujemne ceny energii — ograniczyliśmy produkcję Państwa farmy PV. Wznowienie nastąpi automatycznie.",
                EMAIL_DO_AUTO
            )
            wyslij_whatsapp(TEMPLATE_SID_AUTO_WYL, WHATSAPP_NUMERY_AUTO)

            zapisz_log_alertu("WYŁĄCZENIE", cena, czas)
            poprzednia_cena_niska = True
            zapisz_stan(True)
            zapisz_zadanie_fs(6)
        else:
            print("🔁 Cena nadal niska – bez kolejnych powiadomień.")
    else:
        if poprzednia_cena_niska is True:
            print("✅ Cena wzrosła — wysyłamy powiadomienia do OBU grup klientów...")

            # === GRUPA 1: STARZY KLIENCI (manualne sterowanie) ===
            wyslij_maila(
                "Wartości rozliczeń dodatnie",
                "Wartości rozliczeń za energię elektryczną dodatnie. Zalecamy rozważenie włączenia farmy.",
                EMAIL_DO
            )
            wyslij_whatsapp(TEMPLATE_SID_POWYZEJ, WHATSAPP_NUMERY)

            # === GRUPA 2: NOWI KLIENCI (auto-sterowani, my włączyliśmy) ===
            wyslij_maila(
                "Przywróciliśmy normalną pracę Państwa farmy PV",
                "Dodatnie ceny energii — przywróciliśmy normalną pracę Państwa farmy PV.",
                EMAIL_DO_AUTO
            )
            wyslij_whatsapp(TEMPLATE_SID_AUTO_ZAL, WHATSAPP_NUMERY_AUTO)

            zapisz_log_alertu("WŁĄCZENIE", cena, czas)
            poprzednia_cena_niska = False
            zapisz_stan(False)
            zapisz_zadanie_fs(0)
        else:
            print("🔁 Cena nadal powyżej 30zł – brak akcji.")

# -------------------- Start --------------------
print("🚀 Aplikacja alertowa v2 wystartowała")
print(f"   Grupa MANUALNA: {len(EMAIL_DO)} email, {len(WHATSAPP_NUMERY)} WhatsApp")
print(f"   Grupa AUTO:     {len(EMAIL_DO_AUTO)} email, {len(WHATSAPP_NUMERY_AUTO)} WhatsApp")
poprzednia_cena_niska = wczytaj_stan()

# -------------------- Harmonogram --------------------
while True:
    czas_lokalny = datetime.now(ZoneInfo("Europe/Warsaw"))
    godz = czas_lokalny.hour

    if 5 <= godz < 23:
        try:
            sprawdz_ceny()
        except Exception as e:
            print(f"❌ Błąd główny: {e}")
            traceback.print_exc()
        time.sleep(60)
    else:
        print(f"🌙 Poza godzinami działania (teraz {godz}:00) – pauza 10 min.")
        time.sleep(600)
