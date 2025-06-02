import requests
import smtplib
import json
import os
from email.mime.text import MIMEText
from datetime import datetime
import time
from twilio.rest import Client
import csv
from dotenv import load_dotenv
load_dotenv()

# Konfiguracja e-maila (home.pl)
EMAIL_OD = os.getenv("EMAIL_OD")
EMAIL_DO = os.getenv("EMAIL_DO").split(",")
EMAIL_HASLO = os.getenv("EMAIL_HASLO")
SMTP_SERVER = os.getenv("SMTP_SERVER")
SMTP_PORT = int(os.getenv("SMTP_PORT"))

# WhatsApp (Twilio)
TWILIO_SID = os.getenv("TWILIO_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_FROM = os.getenv("TWILIO_WHATSAPP_FROM")
WHATSAPP_NUMERY = os.getenv("WHATSAPP_NUMERY").split(",")


TEMPLATE_SID_PONIZEJ = os.getenv("TEMPLATE_SID_PONIZEJ")
TEMPLATE_SID_POWYZEJ = os.getenv("TEMPLATE_SID_POWYZEJ")

STAN_ALERTU_FILE = "stan_alertu.json"
LOG_FILE = "log_alertow.csv"

client = Client(TWILIO_SID, TWILIO_AUTH_TOKEN)



def zapisz_log_alertu(typ, cena, czas):
    data = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    wpis = [data, typ, f"{cena:.2f}", czas]

    naglowek = ["Data_wyslania", "Typ_alertu", "Cena", "Okres_czasowy"]

    plik_istnieje = os.path.exists(LOG_FILE)
    try:
        with open(LOG_FILE, "a", newline="") as f:
            writer = csv.writer(f)
            if not plik_istnieje:
                writer.writerow(naglowek)
            writer.writerow(wpis)
    except Exception as e:
        print(f"❌ Błąd zapisu do logu: {e}")

# --- Zapisywanie i odczyt stanu ---
def zapisz_stan(czy_niska):
    try:
        with open(STAN_ALERTU_FILE, "w") as f:
            json.dump({"niska_cena": czy_niska}, f)
    except Exception as e:
        print(f"❌ Błąd zapisu stanu: {e}")

def wczytaj_stan():
    if os.path.exists(STAN_ALERTU_FILE):
        try:
            with open(STAN_ALERTU_FILE, "r") as f:
                dane = json.load(f)
                print(f"✅ Wczytano stan: {dane}")
                return dane.get("niska_cena")
        except Exception as e:
            print(f"❌ Błąd odczytu stanu: {e}")
            return None
    else:
        print("ℹ️ Plik stanu nie istnieje.")
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

# --- WhatsApp (Twilio Templates) ---
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

# --- Sprawdzanie cen ---
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

# --- START skryptu ---
poprzednia_cena_niska = wczytaj_stan()

while True:
    aktualna_godzina = datetime.now().hour
    if 5 <= aktualna_godzina < 23:
        try:
            sprawdz_ceny()
        except Exception as e:
            print(f"❌ Błąd główny: {e}")
        time.sleep(60)  # sprawdzanie co minutę w dozwolonych godzinach
    else:
        print(f"🌙 Poza godzinami działania (teraz {aktualna_godzina}:00) – pauza 10 min.")
        time.sleep(600)  # śpij 10 minut poza zakresem działania
