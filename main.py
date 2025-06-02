import requests
import smtplib
import os
import psycopg2
import csv
import traceback
from email.mime.text import MIMEText
from datetime import datetime
import time
from twilio.rest import Client
from dotenv import load_dotenv

load_dotenv()
print(f"✅ [TEST] RAW env: {os.environ.get('DATABASE_URL')!r}")
print(f"✅ [TEST] Parsed: {DATABASE_URL!r}")

# --- E-mail ---
EMAIL_OD = os.getenv("EMAIL_OD")
EMAIL_DO = os.getenv("EMAIL_DO").split(",")
EMAIL_HASLO = os.getenv("EMAIL_HASLO")
SMTP_SERVER = os.getenv("SMTP_SERVER")
SMTP_PORT = int(os.getenv("SMTP_PORT"))

# --- WhatsApp ---
TWILIO_SID = os.getenv("TWILIO_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_FROM = os.getenv("TWILIO_WHATSAPP_FROM")
WHATSAPP_NUMERY = os.getenv("WHATSAPP_NUMERY").split(",")

TEMPLATE_SID_PONIZEJ = os.getenv("TEMPLATE_SID_PONIZEJ")
TEMPLATE_SID_POWYZEJ = os.getenv("TEMPLATE_SID_POWYZEJ")

# --- Railway DB ---
DATABASE_URL = os.getenv("DATABASE_URL")

client = Client(TWILIO_SID, TWILIO_AUTH_TOKEN)

# --- Zapisywanie i odczyt stanu z PostgreSQL ---
def zapisz_stan(czy_niska):
    print(f"🔧 [DEBUG] Próba zapisania stanu: {czy_niska}")
    print(f"🔧 [DEBUG] DATABASE_URL: {DATABASE_URL}")
    try:
        conn = psycopg2.connect(DATABASE_URL)
        print("✅ [DEBUG] Połączono z bazą danych")
        cur = conn.cursor()
        cur.execute("UPDATE stan_alertu SET czy_niska = %s WHERE id = 1", (czy_niska,))
        conn.commit()
        cur.close()
        conn.close()
        print(f"💾 [DEBUG] Zapisano stan: {czy_niska}")
    except Exception as e:
        print(f"❌ [BŁĄD zapisu stanu] {type(e).__name__}: {str(e)}")
        traceback.print_exc()

def wczytaj_stan():
    print("🔧 [DEBUG] Próba wczytania stanu z bazy...")
    print(f"🔧 [DEBUG] DATABASE_URL: {DATABASE_URL}")
    try:
        conn = psycopg2.connect(DATABASE_URL)
        print("✅ [DEBUG] Połączono z bazą danych")
        cur = conn.cursor()
        cur.execute("SELECT czy_niska FROM stan_alertu WHERE id = 1")
        wynik = cur.fetchone()
        cur.close()
        conn.close()
        if wynik:
            print(f"✅ [DEBUG] Wczytany stan: {wynik[0]}")
            return wynik[0]
        else:
            print("⚠️ [DEBUG] Brak rekordu o id=1 w stan_alertu")
            return None
    except Exception as e:
        print(f"❌ [BŁĄD odczytu stanu] {type(e).__name__}: {str(e)}")
        traceback.print_exc()
        return None

# --- E-mail ---
def wyslij_maila(temat, tresc):
    try:
        for adres in EMAIL_DO:
            msg = MIMEText(tresc)
            msg['Subject'] = temat
            msg['From'] = EMAIL_OD
            msg['To'] = adres
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
                server.starttls()
                server.login(EMAIL_OD, EMAIL_HASLO)
                server.sendmail(EMAIL_OD, adres, msg.as_string())
            print(f"📧 E-mail wysłany do: {adres}")
    except Exception as e:
        print(f"❌ Błąd e-mail: {e}")
        traceback.print_exc()

# --- WhatsApp ---
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

# --- Logi ---
def zapisz_log_alertu(typ, cena, czas):
    print("🔧 [DEBUG] Próba zapisania logu alertu...")
    print(f"🔧 [DEBUG] DATABASE_URL: {DATABASE_URL}")
    print(f"🔧 [DEBUG] Parametry: typ={typ}, cena={cena}, czas={czas}")
    try:
        conn = psycopg2.connect(DATABASE_URL)
        print("✅ [DEBUG] Połączono z bazą danych")
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO log_alertow (data_wyslania, typ_alertu, cena, okres_czasowy)
            VALUES (NOW(), %s, %s, %s)
        """, (typ, cena, czas))
        conn.commit()
        cur.close()
        conn.close()
        print("📝 [DEBUG] Zapisano log alertu do bazy")
    except Exception as e:
        print(f"❌ [BŁĄD zapisu logu] {type(e).__name__}: {str(e)}")
        traceback.print_exc()

# --- Główna logika ---
def sprawdz_ceny():
    global poprzednia_cena_niska

    dzis = datetime.now().strftime('%Y-%m-%d')
    url = f"https://api.raporty.pse.pl/api/crb-prog?$filter=doba eq '{dzis}'"

    try:
        response = requests.get(url)
        response.raise_for_status()
        dane = response.json().get("value", [])
    except Exception as e:
        print(f"❌ Błąd pobierania danych: {e}")
        traceback.print_exc()
        return

    if not dane:
        print("Brak danych z API.")
        return

    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Dane PSE:")
    for rekord in dane:
        cena = rekord.get("cen_prog", 9999)
        czas = rekord.get("udtczas_oreb", "brak")
        print(f"Godzina: {czas} | Cena: {cena} zł")

    ostatni_rekord = dane[-1]
    ostatnia_cena = ostatni_rekord.get("cen_prog", 9999)
    ostatnia_godzina = ostatni_rekord.get("udtczas_oreb", "brak")

    print(f"\n➡️ Ostatnia cena: {ostatnia_cena} zł o {ostatnia_godzina}")

    if ostatnia_cena <= 30:
        if poprzednia_cena_niska is not True:
            print("⚠️ Cena niska - wysyłamy powiadomienia o wyłączeniu farmy...")
            temat = "UWAGA Możliwe ujemne wartości"
            tresc = "UWAGA! Możliwe ujemne wartości rozliczeń za energię elektryczną. Zalecamy wyłączenie farmy."
            wyslij_maila(temat, tresc)
            wyslij_whatsapp(TEMPLATE_SID_PONIZEJ)
            zapisz_log_alertu("WYŁĄCZENIE", ostatnia_cena, ostatnia_godzina)
            poprzednia_cena_niska = True
            zapisz_stan(True)
        else:
            print("Cena nadal niska – bez kolejnych powiadomień.")
    else:
        if poprzednia_cena_niska is True:
            print("✅ Cena wzrosła – wysyłamy powiadomienia o możliwości włączenia farmy...")
            temat = "Wartości rozliczeń dodatnie"
            tresc = "Wartośći rozliczeń za energię elektryczną dodatnie. Zalecamy rozważenie włączenia farmy."
            wyslij_maila(temat, tresc)
            wyslij_whatsapp(TEMPLATE_SID_POWYZEJ)
            zapisz_log_alertu("WŁĄCZENIE", ostatnia_cena, ostatnia_godzina)
            poprzednia_cena_niska = False
            zapisz_stan(False)
        else:
            print("Cena nadal powyżej 30zł – brak akcji.")

# --- Start ---
poprzednia_cena_niska = wczytaj_stan()
zapisz_log_alertu("DIAGNOSTYKA", 99.99, "10:45 - 11:00")
exit()

while True:
    aktualna_godzina = datetime.now().hour
    if 5 <= aktualna_godzina < 23:
        try:
            sprawdz_ceny()
        except Exception as e:
            print(f"❌ Błąd główny: {e}")
            traceback.print_exc()
        time.sleep(60)
    else:
        print(f"🌙 Poza godzinami działania (teraz {aktualna_godzina}:00) – pauza 10 min.")
        time.sleep(600)
