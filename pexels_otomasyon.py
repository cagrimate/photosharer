import os
import sys
import requests
import tweepy
from dotenv import load_dotenv
import random
import time
import schedule
from google import genai
from google.genai.errors import APIError
from google.genai import types

# ----------------------------------------------------
# 1. AYARLAR VE API ANAHTARLARI
# ----------------------------------------------------
load_dotenv()

PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

ID_TRACKER_FILE = "paylasilan_idler.txt"
TEMP_GEMINI_IMAGE = "temp_tiny_photo.jpg"
TEMP_TWITTER_IMAGE = "temp_original_photo.jpg"

GEMINI_CLIENT = None
if GEMINI_API_KEY:
    try:
        GEMINI_CLIENT = genai.Client(api_key=GEMINI_API_KEY)
        print("Gemini API bağlantısı başarılı.")
    except Exception as e:
        print(f"❌ GEMINI_API_KEY bağlantı hatası: {e}")

# ----------------------------------------------------
# 2. TWITTER BAĞLANTISI
# ----------------------------------------------------
def get_twitter_client():
    try:
        client = tweepy.Client(
            consumer_key=os.getenv("CONSUMER_KEY"),
            consumer_secret=os.getenv("CONSUMER_SECRET"),
            access_token=os.getenv("ACCESS_TOKEN"),
            access_token_secret=os.getenv("ACCESS_TOKEN_SECRET")
        )
        auth = tweepy.OAuthHandler(os.getenv("CONSUMER_KEY"), os.getenv("CONSUMER_SECRET"))
        auth.set_access_token(os.getenv("ACCESS_TOKEN"), os.getenv("ACCESS_TOKEN_SECRET"))
        api_v1 = tweepy.API(auth)
        client.get_me()
        return client, api_v1
    except Exception as e:
        print(f"❌ Twitter bağlantı hatası: {e}")
        return None, None

# ----------------------------------------------------
# 3. LOJİK VE DOSYA TAKİBİ
# ----------------------------------------------------
def get_shared_ids():
    if not os.path.exists(ID_TRACKER_FILE):
        return set()
    try:
        with open(ID_TRACKER_FILE, "r", encoding="utf-8") as f:
            return set(line.strip() for line in f)
    except Exception:
        return set()

def add_id_to_tracker(photo_id):
    try:
        with open(ID_TRACKER_FILE, "a", encoding="utf-8") as f:
            f.write(f"{photo_id}\n")
    except Exception as e:
        print(f"ID dosyasına yazma hatası: {e}")

# ----------------------------------------------------
# 4. PEXELS API
# ----------------------------------------------------
def fetch_unique_photo_data(shared_ids):
    print("📷 Yeni fotoğraf aranıyor...")
    if not PEXELS_API_KEY:
        return None
    
    categories = [
        "cinematic", "street photography", "dark moody", "abstract art", 
        "minimalist", "cyberpunk", "foggy forest", "urban aesthetic", 
        "film noir", "night city", "surreal", "vintage style"
    ]

    attempts = 0
    max_attempts = 30 

    while attempts < max_attempts:
        attempts += 1
        selected_category = random.choice(categories)
        page_num = random.randint(1, 100)
        headers = {"Authorization": PEXELS_API_KEY}
        url = f"https://api.pexels.com/v1/search?query={selected_category}&per_page=1&page={page_num}"

        try:
            res = requests.get(url, headers=headers, timeout=15)
            res.raise_for_status()
            data = res.json()
            if data.get("photos"):
                photo = data["photos"][0]
                photo_id_str = str(photo["id"])
                if photo_id_str not in shared_ids:
                    return {
                        "id": photo_id_str,
                        "url_tiny": photo["src"]["tiny"],
                        "url_original": photo["src"]["original"],
                        "photographer": photo["photographer"],
                    }
            time.sleep(1)
        except Exception as e:
            print(f"Pexels hatası: {e}")
            continue
    return None

def download_image(url, filename):
    try:
        res = requests.get(url, stream=True, timeout=30)
        res.raise_for_status()
        with open(filename, "wb") as f:
            for chunk in res.iter_content(chunk_size=8192):
                f.write(chunk)
        return filename
    except Exception as e:
        print(f"İndirme hatası: {e}")
        return None

# ----------------------------------------------------
# 5. GEMINI CAPTION ÜRETİCİ (DÜZENLENDİ)
# ----------------------------------------------------
def generate_ai_caption(photo_data, image_path):
    MAX_X_LIMIT = 280
    FOOTER = f"\n\n📸 {photo_data['photographer']} #Ai #Visual"
    SAFE_LIMIT = MAX_X_LIMIT - len(FOOTER) - 10 

    def static_caption():
        return (f"Visual escape. 📸 {photo_data['photographer']} #Visual")[:280]

    if not GEMINI_CLIENT or not image_path or not os.path.exists(image_path):
        return static_caption()

    # Model isimlerini isteğin üzerine değiştirmedim
    MODELS = ["gemini-2.5-flash", "gemini-2.5-pro"] 

    try:
        # Dosyayı güvenli açma (with bloğu)
        with open(image_path, "rb") as f:
            img_bytes = f.read()
    except Exception:
        return static_caption()

    for model in MODELS:
        print(f"🤖 Gemini modeli deneniyor: {model}")
        for attempt in range(1, 3):
            try:
                prompt = (
                    f"Write a cinematic tweet about this image in max {SAFE_LIMIT} characters. "
                    "Structure: One sharp hook, one visual description, one tiny story. "
                    "Rules: NO labels (like Hook:), NO emojis, NO headers, NO hashtags. Plain text only."
                )

                response = GEMINI_CLIENT.models.generate_content(
                    model=model,
                    contents=[
                        types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg"),
                        prompt,
                    ],
                )

                caption = (response.text or "").strip()
                if not caption:
                    continue

                # Model başlık eklerse temizle
                for tag in ["Hook:", "Description:", "Story:", "Caption:", "**", "Twitter Caption:"]:
                    caption = caption.replace(tag, "")
                
                caption = caption.strip()

                # Karakter Kontrolü ve Sert Kesme
                if len(caption) + len(FOOTER) > MAX_X_LIMIT:
                    allowed_caption_len = MAX_X_LIMIT - len(FOOTER) - 3
                    caption = caption[:allowed_caption_len] + "..."

                final_text = f"{caption}{FOOTER}"
                print(f"✨ Tweet Hazır ({len(final_text)} karakter)")
                return final_text

            except Exception as e:
                print(f"⚠️ {model} deneme {attempt} hatası: {e}")
                time.sleep(2)
                continue

    return static_caption()

# ----------------------------------------------------
# 6. ANA ÇALIŞTIRMA FONKSİYONU
# ----------------------------------------------------
def run_bot_task():
    print(f"\n🚀 BOT ÇALIŞTI — {time.strftime('%H:%M:%S')}")

    client, api_v1 = get_twitter_client()
    if not client or not api_v1:
        return

    shared = get_shared_ids()
    photo = fetch_unique_photo_data(shared)

    if not photo:
        return

    try:
        tiny = download_image(photo["url_tiny"], TEMP_GEMINI_IMAGE)
        original = download_image(photo["url_original"], TEMP_TWITTER_IMAGE)

        if not tiny or not original:
            return

        caption = generate_ai_caption(photo, tiny)

        print("🔗 Medya yükleniyor...")
        media = api_v1.media_upload(filename=original)

        print("🐦 Tweet atılıyor...")
        tw = client.create_tweet(text=caption, media_ids=[media.media_id_string])

        add_id_to_tracker(photo["id"])
        print(f"🎉 Tweet atıldı! ID: {tw.data['id']}")

    except Exception as e:
        print(f"❌ İşlem Hatası: {e}")

    finally:
        # Dosyaların kilitlenmemesi için with bloğu kullandık, şimdi silebiliriz
        for f in [TEMP_GEMINI_IMAGE, TEMP_TWITTER_IMAGE]:
            if os.path.exists(f):
                os.remove(f)
        print("🧹 Temizlik yapıldı.")

# ----------------------------------------------------
# 7. ZAMANLAYICI
# ----------------------------------------------------
if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1].lower() == "once":
        run_bot_task()
    else:
        run_bot_task()
        schedule.every(90).minutes.do(run_bot_task)
        print("\n🟢 BOT AKTİF (90 dakikada bir çalışacak)\n")
        while True:
            schedule.run_pending()
            time.sleep(1)
