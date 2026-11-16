import json
import os
import time
import urllib.parse
import folium
from folium.plugins import MarkerCluster
from geopy.geocoders import Nominatim
from playwright.sync_api import sync_playwright

# --- CONFIGURATION ---
KEYWORDS = ["achats", "buyer", "purchasing", "procurement", "sourcing", "approvisionnement"]
CACHE_FILE = "geo_cache.json"
OUTPUT_HTML = "index.html"

if os.path.exists(CACHE_FILE):
    with open(CACHE_FILE, "r", encoding="utf-8") as f:
        geo_cache = json.load(f)
else:
    geo_cache = {}

geolocator = Nominatim(user_agent="vie_achats_tracker_v1")

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
        location_country = geolocator.geocode(country, timeout=10)
        if location_country:
            coords = [location_country.latitude, location_country.longitude]
            geo_cache[query] = coords
            return coords
    except Exception as e:
        print(f"Erreur géocodage pour {query}: {e}")
    return None

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
                        items = data.get("items", data.get("results", data.get("offres", [])))
                    elif isinstance(data, list):
                        items = data
                    
                    for item in items:
                        if isinstance(item, dict) and ("missionTitle" in item or "titre" in item or "title" in item):
                            offer_id = str(item.get("id") or item.get("reference") or item.get("missionId"))
                            if offer_id and offer_id not in offers_dict:
                                title = item.get("missionTitle") or item.get("titre") or item.get("title", "Offre VIE")
                                company = item.get("organizationName") or item.get("entreprise") or "Entreprise confidentielle"
                                city = item.get("cityName") or item.get("ville") or ""
                                country = item.get("countryName") or item.get("pays") or ""
                                duration = item.get("duration") or item.get("duree") or "N/A"
                                start_date = str(item.get("startDate") or item.get("dateDebut") or "Dès que possible")[:10]
                                slug = item.get("url") or f"https://mon-vie-via.businessfrance.fr/offres/{offer_id}"
                                if not slug.startswith("http"):
                                    slug = f"https://mon-vie-via.businessfrance.fr{slug}"
                                
                                offers_dict[offer_id] = {
                                    "id": offer_id,
                                    "title": title,
                                    "company": company,
                                    "city": city,
                                    "country": country,
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
                        if offer_id not in offers_dict:
                            lines = [l.strip() for l in text.split("\n") if l.strip()]
                            offers_dict[offer_id] = {
                                "id": offer_id,
                                "title": lines[0] if len(lines) > 0 else "VIE Achats",
                                "company": lines[1] if len(lines) > 1 else "Entreprise",
                                "city": lines[2] if len(lines) > 2 else "",
                                "country": lines[3] if len(lines) > 3 else "",
                                "duration": "12-24 mois",
                                "start_date": "Voir annonce",
                                "url": f"https://mon-vie-via.businessfrance.fr{href}" if href.startswith("/") else href
                            }
            except Exception as e:
                print(f"Erreur sur {kw}: {e}")

        browser.close()
    return list(offers_dict.values())

def build_map(offers):
    m = folium.Map(location=[25.0, 10.0], zoom_start=2, tiles="CartoDB positron")
    marker_cluster = MarkerCluster().add_to(m)

    valid_count = 0
    for offer in offers:
        coords = get_coordinates(offer["city"], offer["country"])
        if coords:
            valid_count += 1
            popup_html = f"""
            <div style="font-family: Arial, sans-serif; width: 250px;">
                <h4 style="margin: 0 0 5px 0; color: #0055A4;">{offer['title']}</h4>
                <p style="margin: 2px 0;"><b>🏢 Entreprise :</b> {offer['company']}</p>
                <p style="margin: 2px 0;"><b>📍 Lieu :</b> {offer['city']} ({offer['country']})</p>
                <p style="margin: 2px 0;"><b>⏳ Durée :</b> {offer['duration']} mois</p>
                <p style="margin: 2px 0 10px 0;"><b>📅 Début :</b> {offer['start_date']}</p>
                <a href="{offer['url']}" target="_blank" style="display: inline-block; padding: 6px 12px; background-color: #EF4135; color: white; text-decoration: none; border-radius: 4px; font-weight: bold;">Voir l'offre & Postuler</a>
            </div>
            """
            folium.Marker(
                location=coords,
                popup=folium.Popup(popup_html, max_width=300),
                tooltip=f"{offer['title']} - {offer['city']}",
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
    build_map(all_offers)
