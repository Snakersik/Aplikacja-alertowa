import requests
import smtplib
import os
import psycopg2
from email.mime.text import MIMEText
from datetime import datetime
import time
from twilio.rest import Client
from dotenv import load_dotenv
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo
import ssl, socket

load_dotenv()

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
DATABASE_URL = os.getenv("DATABASE_URL").strip()

client = Client(TWILIO_SID, TWILIO_AUTH_TOKEN)

# --- Zapisywanie i odczyt stanu ---
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

# --- E-mail ---
def wyslij_maila(temat, tresc):
    to_list = [a.strip() for a in os.getenv("EMAIL_DO","").split(",") if a.strip()]
    if not to_list:
        print("⚠️ Brak odbiorców EMAIL_DO")
        return

    msg = MIMEText(tresc, _charset="utf-8")
    msg["Subject"] = temat
    msg["From"] = EMAIL_OD
    msg["To"] = ", ".join(to_list)

    ctx = ssl.create_default_context()

    # Użyj wartości z .env, ale miej fallbacki gdyby łączenie się nie udało
    primary = (os.getenv("SMTP_SERVER") or "smtp.home.pl", int(os.getenv("SMTP_PORT") or 465))
    candidates = [
        (*primary, "auto"),
        ("smtp.home.pl", 465, "ssl"),
        ("smtp.home.pl", 587, "starttls"),
        ("serwer2003197.home.pl", 465, "ssl"),
        ("serwer2003197.home.pl", 587, "starttls"),
    ]

    last_err = None
    for host, port, mode in candidates:
        try:
            # wymuszamy IPv4 (częsty problem po restarcie z IPv6)
            ipv4 = socket.getaddrinfo(host, port, socket.AF_INET)[0][4][0]
            print(f"↪️ Próba SMTP {host}:{port} ({mode})")
            if mode == "ssl" or (mode == "auto" and port == 465):
                with smtplib.SMTP_SSL(ipv4, port, context=ctx, timeout=20) as server:
                    server.login(EMAIL_OD, EMAIL_HASLO)
                    server.sendmail(EMAIL_OD, to_list, msg.as_string())
                print("✅ E-mail wysłany (SMTPS 465).")
                return
            else:  # STARTTLS 587
                with smtplib.SMTP(ipv4, port, timeout=20) as server:
                    server.ehlo()
                    server.starttls(context=ctx)
                    server.ehlo()
                    server.login(EMAIL_OD, EMAIL_HASLO)
                    server.sendmail(EMAIL_OD, to_list, msg.as_string())
                print("✅ E-mail wysłany (STARTTLS 587).")
                return
        except Exception as e:
            print(f"❌ Nieudana próba {host}:{port} – {e}")
            last_err = e

    raise RuntimeError(f"Nie udało się wysłać e-maila żadnym sposobem: {last_err}")

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

# --- Logika ---
def sprawdz_ceny():
    global poprzednia_cena_niska
    dzis = datetime.now().strftime('%Y-%m-%d')
    url = (
        "https://apimpdv2-bmgdhhajexe8aade.a01.azurefd.net/api/price-fcst"
        f"?$filter=business_date eq '{dzis}'"
        "&$orderby=dtime asc"
    )

    try:
        response = requests.get(url)
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
            wyslij_maila("UWAGA Możliwe ujemne wartości",
                         "UWAGA! Możliwe ujemne wartości rozliczeń za energię elektryczną. Zalecamy wyłączenie farmy.")
            wyslij_whatsapp(TEMPLATE_SID_PONIZEJ)
            zapisz_log_alertu("WYŁĄCZENIE", cena, czas)
            poprzednia_cena_niska = True
            zapisz_stan(True)
        else:
            print("🔁 Cena nadal niska – bez kolejnych powiadomień.")
    else:
        if poprzednia_cena_niska is True:
            print("✅ Cena wzrosła – wysyłamy powiadomienia o możliwości włączenia farmy...")
            wyslij_maila("Wartości rozliczeń dodatnie",
                         "Wartośći rozliczeń za energię elektryczną dodatnie. Zalecamy rozważenie włączenia farmy.")
            wyslij_whatsapp(TEMPLATE_SID_POWYZEJ)
            zapisz_log_alertu("WŁĄCZENIE", cena, czas)
            poprzednia_cena_niska = False
            zapisz_stan(False)
        else:
            print("🔁 Cena nadal powyżej 30zł – brak akcji.")

# --- Start ---
print("🚀 Aplikacja alertowa wystartowała")
poprzednia_cena_niska = wczytaj_stan()

# --- Harmonogram ---
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