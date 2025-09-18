# main.py — wersja z wysyłką przez Twilio SendGrid (HTTP API)

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
EMAIL_DO = [a.strip() for a in (os.getenv("EMAIL_DO") or "").split(",") if a.strip()]

# --- WhatsApp (Twilio) ---
TWILIO_SID = os.getenv("TWILIO_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_FROM = os.getenv("TWILIO_WHATSAPP_FROM")
WHATSAPP_NUMERY = [n.strip() for n in (os.getenv("WHATSAPP_NUMERY") or "").split(",") if n.strip()]

TEMPLATE_SID_PONIZEJ = os.getenv("TEMPLATE_SID_PONIZEJ")
TEMPLATE_SID_POWYZEJ = os.getenv("TEMPLATE_SID_POWYZEJ")

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
def wyslij_maila(temat, tresc):
    if not SENDGRID_API_KEY:
        print("❌ Brak SENDGRID_API_KEY – nie wyślę e-maila.")
        return
    if not EMAIL_OD:
        print("❌ Brak EMAIL_OD – nie wyślę e-maila.")
        return

    to_list = [a.strip() for a in (os.getenv("EMAIL_DO") or "").split(",") if a.strip()]
    if not to_list:
        print("⚠️ Brak odbiorców EMAIL_DO")
        return

    # Każdy odbiorca w osobnej personalizacji → nikt nie widzi innych
    personalizations = [{"to": [{"email": addr}]} for addr in to_list]

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
            print(f"✅ E-mail wysłany (SendGrid) do {len(to_list)} odbiorców.")
        else:
            print(f"❌ SendGrid zwrócił {r.status_code}: {r.text}")
    except Exception as e:
        print(f"❌ Błąd HTTP przy wysyłce e-maila: {e}")
        traceback.print_exc()

def wyslij_mail_info_migracja():
    """Jednorazowa wiadomość informująca o zmianie sposobu wysyłki (prośba o 'OK')."""
    temat = "Aktualizacja sposobu wysyłki powiadomień – prosimy o krótkie potwierdzenie"
    tresc = (
        "Dzień dobry,\n\n"
        "Od dziś wysyłamy powiadomienia e-mail przez zabezpieczoną platformę "
        ".Nadawca i treść pozostają bez zmian — "
        "a jej celem jest zwiększenie niezawodności dostarczania po niedawnym incydencie "
        "sieciowym u dostawcy hostingu.\n\n"
        "Uprzejmie prosimy o krótką odpowiedź, aby potwierdzić odbiór tej wiadomości.\n\n"
        "Nadawca: Alert <alert@techion.com.pl>\n"
        "Jeśli wiadomość trafiła do zakładki Oferty/Spam, prosimy dodać adres nadawcy do zaufanych.\n\n"
        "Pozdrawiamy,\n"
        "Zespół Techion"
    )
    wyslij_maila(temat, tresc)

# -------------------- WhatsApp --------------------
def wyslij_whatsapp(content_sid):
    try:
        for numer in WHATSAPP_NUMERY:
            client.messages.create(
                to=numer,
                from_=TWILIO_WHATSAPP_FROM,
                content_sid=content_sid
            )
            print(f"📲 WhatsApp wysłany do: {numer}")
    except Exception as e:
        print(f"❌ Błąd WhatsApp: {e}")
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
        cena = rekord.get("cen_fcst", 9999)
        czas = rekord.get("period", rekord.get("dtime", "brak"))
        print(f"Godzina: {czas} | Cena: {cena} zł")

    ostatni_rekord = dane[-1]
    cena = ostatni_rekord.get("cen_fcst", 9999)
    czas = ostatni_rekord.get("period", ostatni_rekord.get("dtime", "brak"))

    print(f"\n➡️ Ostatnia cena: {cena} zł o {czas}")

    if cena <= 30:
        if poprzednia_cena_niska is not True:
            print("⚠️ Cena niska - wysyłamy powiadomienia o wyłączeniu farmy...")
            wyslij_maila(
                "UWAGA Możliwe ujemne wartości",
                "UWAGA! Możliwe ujemne wartości rozliczeń za energię elektryczną. Zalecamy wyłączenie farmy."
            )
            wyslij_whatsapp(TEMPLATE_SID_PONIZEJ)
            zapisz_log_alertu("WYŁĄCZENIE", cena, czas)
            poprzednia_cena_niska = True
            zapisz_stan(True)
        else:
            print("🔁 Cena nadal niska – bez kolejnych powiadomień.")
    else:
        if poprzednia_cena_niska is True:
            print("✅ Cena wzrosła – wysyłamy powiadomienia o możliwości włączenia farmy...")
            wyslij_maila(
                "Wartości rozliczeń dodatnie",
                "Wartośći rozliczeń za energię elektryczną dodatnie. Zalecamy rozważenie włączenia farmy."
            )
            wyslij_whatsapp(TEMPLATE_SID_POWYZEJ)
            zapisz_log_alertu("WŁĄCZENIE", cena, czas)
            poprzednia_cena_niska = False
            zapisz_stan(False)
        else:
            print("🔁 Cena nadal powyżej 30zł – brak akcji.")

# -------------------- Start --------------------
print("🚀 Aplikacja alertowa wystartowała")
poprzednia_cena_niska = wczytaj_stan()

# --- Jednorazowy komunikat o migracji (ustaw SEND_INFO_ONCE=1 w .env i uruchom raz) ---
if os.getenv("SEND_INFO_ONCE", "0") == "1":
    print("📣 Wysyłam jednorazową informację o migracji wysyłki e-mail…")
    wyslij_mail_info_migracja()

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
