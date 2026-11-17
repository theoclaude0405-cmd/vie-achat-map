import json
import os
import re
import smtplib
import time
import urllib.parse
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import folium
from folium.plugins import MarkerCluster
from geopy.geocoders import Nominatim
from playwright.sync_api import sync_playwright

# --- CONFIGURATION ---
KEYWORDS = ["achats", "buyer", "purchasing", "procurement", "sourcing", "approvisionnement"]
CACHE_FILE = "geo_cache.json"
SEEN_FILE = "seen_offers.json"
OUTPUT_HTML = "index.html"

EMAIL_SENDER = os.environ.get("EMAIL_SENDER")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD")
EMAIL_RECEIVER = EMAIL_SENDER

geo_cache = json.load(open(CACHE_FILE, "r", encoding="utf-8")) if os.path.exists(CACHE_FILE) else {}
seen_offers = set(json.load(open(SEEN_FILE, "r", encoding="utf-8"))) if os.path.exists(SEEN_FILE) else set()

geolocator = Nominatim(user_agent="vie_achats_tracker_v2")

def get_coordinates(city, country):
    query = f"{city}, {country}".strip(", ")
    if not query:
        return None
    if query in geo_cache:
        return geo_cache[query]
    
    try:
        time.sleep(1.1)
        location = geolocator.geocode(query, timeout=10)
        if location:
            coords = [location.latitude, location.longitude]
            geo_cache[query] = coords
            return coords
        if country:
            time.sleep(1.1)
            location_country = geolocator.geocode(country, timeout=10)
            if location_country:
                coords = [location_country.latitude, location_country.longitude]
                geo_cache[query] = coords
                return coords
    except Exception as e:
        print(f"Erreur géocodage pour '{query}': {e}")
    return None

def send_email_alert(new_offers):
    if not EMAIL_SENDER or not EMAIL_PASSWORD or not new_offers:
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🌍 {len(new_offers)} nouvelle(s) offre(s) VIE Achats détectée(s) !"
    msg["From"] = EMAIL_SENDER
    msg["To"] = EMAIL_RECEIVER

    html_items = ""
    for o in new_offers:
        loc_str = f"{o['city']} ({o['country']})".strip(" ()") or "Lieu précisé dans l'annonce"
        html_items += f"""
        <div style="border-left: 4px solid #0055A4; padding-left: 12px; margin-bottom: 16px;">
            <h3 style="margin: 0; color: #0055A4;">{o['title']}</h3>
            <p style="margin: 4px 0; color: #333;"><b>🏢 Entreprise :</b> {o['company']}</p>
            <p style="margin: 4px 0; color: #333;"><b>📍 Lieu :</b> {loc_str}</p>
            <p style="margin: 4px 0 8px 0; color: #555;"><b>⏳ Durée :</b> {o['duration']} | <b>📅 Début :</b> {o['start_date']}</p>
            <a href="{o['url']}" style="background-color: #EF4135; color: white; padding: 6px 12px; text-decoration: none; border-radius: 4px; font-size: 13px; font-weight: bold;">Voir l'offre sur Business France</a>
        </div>
        """

    html_body = f"""
    <html>
      <body style="font-family: Arial, sans-serif; line-height: 1.5;">
        <h2>Nouvelles opportunités VIE Achats</h2>
        <p>Voici les dernières offres publiées depuis la dernière vérification :</p>
        {html_items}
      </body>
    </html>
    """
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())
        print(f"E-mail d'alerte envoyé avec {len(new_offers)} offre(s).")
    except Exception as e:
        print(f"Erreur lors de l'envoi de l'e-mail : {e}")

def extract_location_from_text(lines):
    # Cherche une ligne contenant "Ville - Pays" ou "Ville, Pays" ou un pays en majuscules
    for line in lines:
        if " - " in line and len(line) < 60:
            parts = line.split(" - ")
            return parts[0].strip(), parts[-1].strip()
        if ", " in line and len(line) < 60 and not any(w in line.lower() for w in ["mois", "vie", "via", "bac"]):
            parts = line.split(", ")
            return parts[0].strip(), parts[-1].strip()
    # Sinon prend les dernières lignes courtes de la carte
    short_lines = [l for l in lines[1:] if 2 < len(l) < 35 and "mois" not in l.lower() and "publiée" not in l.lower()]
    if len(short_lines) >= 2:
        return short_lines[-2], short_lines[-1]
    elif len(short_lines) == 1:
        return "", short_lines[0]
    return "", ""

def scrape_offers():
    offers_dict = {}
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        def handle_response(response):
            if "api" in response.url and response.status == 200:
                try:
                    data = response.json()
                    items = []
                    if isinstance(data, dict):
                        for key in ["items", "results", "offres", "data", "missions"]:
                            if isinstance(data.get(key), list):
                                items = data[key]
                                break
                    elif isinstance(data, list):
                        items = data
                    
                    for item in items:
                        if isinstance(item, dict) and any(k in item for k in ["missionTitle", "titre", "title", "intitule"]):
                            offer_id = str(item.get("id") or item.get("reference") or item.get("missionId") or item.get("numero"))
                            if offer_id and offer_id != "None":
                                title = item.get("missionTitle") or item.get("titre") or item.get("title") or item.get("intitule") or "Offre VIE"
                                company = item.get("organizationName") or item.get("entreprise") or item.get("companyName") or "Entreprise"
                                
                                # Extraction flexible de la ville et du pays (y compris si imbriqués dans un sous-objet)
                                city = item.get("cityName") or item.get("ville") or item.get("city") or ""
                                if isinstance(city, dict):
                                    city = city.get("name") or city.get("libelle") or ""
                                
                                country = item.get("countryName") or item.get("pays") or item.get("country") or ""
                                if isinstance(country, dict):
                                    country = country.get("name") or country.get("libelle") or ""

                                duration = str(item.get("duration") or item.get("duree") or "12-24") + " mois"
                                start_date = str(item.get("startDate") or item.get("dateDebut") or "Dès que possible")[:10]
                                slug = item.get("url") or f"https://mon-vie-via.businessfrance.fr/offres/{offer_id}"
                                if not slug.startswith("http"):
                                    slug = f"https://mon-vie-via.businessfrance.fr{slug}"
                                
                                offers_dict[offer_id] = {
                                    "id": offer_id,
                                    "title": str(title).strip(),
                                    "company": str(company).strip(),
                                    "city": str(city).strip(),
                                    "country": str(country).strip(),
                                    "duration": duration,
                                    "start_date": start_date,
                                    "url": slug
                                }
                except Exception:
                    pass

        page.on("response", handle_response)

        for kw in KEYWORDS:
            encoded_kw = urllib.parse.quote(kw)
            url = f"https://mon-vie-via.businessfrance.fr/offres/recherche?query={encoded_kw}"
            print(f"Recherche en cours pour : {kw}...")
            try:
                page.goto(url, wait_until="networkidle", timeout=30000)
                page.wait_for_timeout(3000)
                
                cards = page.locator("a[href*='/offres/']").all()
                for card in cards:
                    href = card.get_attribute("href")
                    text = card.inner_text().strip()
                    if href and "/offres/recherche" not in href and len(text) > 10:
                        offer_id = href.split("/")[-1]
                        lines = [l.strip() for l in text.split("\n") if l.strip()]
                        city, country = extract_location_from_text(lines)
                        
                        if offer_id not in offers_dict:
                            offers_dict[offer_id] = {
                                "id": offer_id,
                                "title": lines[0] if len(lines) > 0 else "VIE Achats",
                                "company": lines[1] if len(lines) > 1 else "Entreprise",
                                "city": city,
                                "country": country,
                                "duration": "12-24 mois",
                                "start_date": "Voir annonce",
                                "url": f"https://mon-vie-via.businessfrance.fr{href}" if href.startswith("/") else href
                            }
                        else:
                            # Complète la ville/pays si l'API ne les avait pas fournis
                            if not offers_dict[offer_id]["city"] and city:
                                offers_dict[offer_id]["city"] = city
                            if not offers_dict[offer_id]["country"] and country:
                                offers_dict[offer_id]["country"] = country
            except Exception as e:
                print(f"Erreur sur {kw}: {e}")

        browser.close()
    return list(offers_dict.values())

def build_map(offers):
    # Utilisation de OpenStreetMap (100% gratuit, sans clé API requise)
    m = folium.Map(location=[25.0, 10.0], zoom_start=2, tiles="OpenStreetMap")
    marker_cluster = MarkerCluster().add_to(m)

    valid_count = 0
    for offer in offers:
        print(f" -> Analyse : {offer['title']} | Ville='{offer['city']}' | Pays='{offer['country']}'")
        coords = get_coordinates(offer["city"], offer["country"])
        if coords:
            valid_count += 1
            loc_label = f"{offer['city']} ({offer['country']})".strip(" ()")
            popup_html = f"""
            <div style="font-family: Arial, sans-serif; width: 250px;">
                <h4 style="margin: 0 0 5px 0; color: #0055A4;">{offer['title']}</h4>
                <p style="margin: 2px 0;"><b>🏢 Entreprise :</b> {offer['company']}</p>
                <p style="margin: 2px 0;"><b>📍 Lieu :</b> {loc_label}</p>
                <p style="margin: 2px 0;"><b>⏳ Durée :</b> {offer['duration']}</p>
                <p style="margin: 2px 0 10px 0;"><b>📅 Début :</b> {offer['start_date']}</p>
                <a href="{offer['url']}" target="_blank" style="display: inline-block; padding: 6px 12px; background-color: #EF4135; color: white; text-decoration: none; border-radius: 4px; font-weight: bold;">Voir l'offre & Postuler</a>
            </div>
            """
            folium.Marker(
                location=coords,
                popup=folium.Popup(popup_html, max_width=300),
                tooltip=f"{offer['title']} - {loc_label}",
                icon=folium.Icon(color="blue", icon="briefcase", prefix="fa")
            ).add_to(marker_cluster)

    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(geo_cache, f, ensure_ascii=False, indent=2)

    title_html = f"""
    <div style="position: fixed; top: 10px; left: 50px; z-index: 9999; background-color: white; padding: 10px 15px; border-radius: 8px; box-shadow: 0 2px 6px rgba(0,0,0,0.2); font-family: Arial, sans-serif;">
        <h3 style="margin: 0; color: #1a1a1a;">🌍 Radar VIE Achats & Supply</h3>
        <p style="margin: 4px 0 0 0; font-size: 13px; color: #555;"><b>{valid_count}</b> offres géolocalisées • Mis à jour le {time.strftime('%d/%m/%Y à %H:%M')}</p>
    </div>
    """
    m.get_root().html.add_child(folium.Element(title_html))
    m.save(OUTPUT_HTML)
    print(f"Carte générée avec {valid_count} offres dans {OUTPUT_HTML}")

if __name__ == "__main__":
    print("Démarrage de la collecte VIE Achats...")
    all_offers = scrape_offers()
    print(f"{len(all_offers)} offres uniques trouvées.")
    
    new_offers = [o for o in all_offers if o["id"] not in seen_offers]
    if new_offers:
        print(f"{len(new_offers)} nouvelle(s) offre(s) détectée(s) !")
        send_email_alert(new_offers)
        for o in new_offers:
            seen_offers.add(o["id"])
        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(list(seen_offers), f, ensure_ascii=False, indent=2)
    else:
        print("Aucune nouvelle offre depuis la dernière vérification.")

    build_map(all_offers)
