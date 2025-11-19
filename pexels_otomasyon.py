import os
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

# Global Gemini Client başlatma
GEMINI_CLIENT = None
if GEMINI_API_KEY:
    try:
        GEMINI_CLIENT = genai.Client(api_key=GEMINI_API_KEY)
        print("Gemini API bağlantısı başarılı.")
    except Exception as e:
        print(f"❌ GEMINI_API_KEY bağlantı hatası: {e}")
else:
    # Hata düzeltildi: print(⚠️ yerine print("⚠️
    print("⚠️ GEMINI_API_KEY ortam değişkeni ayarlanmamış.")


# ----------------------------------------------------
# 2. TWITTER BAĞLANTISI
# ----------------------------------------------------
def get_twitter_client():
    """Twitter API v2 Client (Tweet) ve v1 API (Medya) nesnelerini döndürür."""
    try:
        # v2 Client (Tweet atma)
        client = tweepy.Client(
            consumer_key=os.getenv("CONSUMER_KEY"),
            consumer_secret=os.getenv("CONSUMER_SECRET"),
            access_token=os.getenv("ACCESS_TOKEN"),
            access_token_secret=os.getenv("ACCESS_TOKEN_SECRET")
        )

        # v1 API (Medya yükleme)
        auth = tweepy.OAuthHandler(os.getenv("CONSUMER_KEY"), os.getenv("CONSUMER_SECRET"))
        auth.set_access_token(os.getenv("ACCESS_TOKEN"), os.getenv("ACCESS_TOKEN_SECRET"))
        api_v1 = tweepy.API(auth)

        # Basit bağlantı testi
        client.get_me()

        return client, api_v1

    except Exception as e:
        print(f"❌ Twitter bağlantısı kurulamadı. Lütfen .env dosyanızı kontrol edin: {e}")
        return None, None


# ----------------------------------------------------
# 3. LOJİK VE DOSYA TAKİBİ
# ----------------------------------------------------
def get_shared_ids():
    """Daha önce paylaşılan fotoğraf ID'lerini okur."""
    if not os.path.exists(ID_TRACKER_FILE):
        return set()
    try:
        with open(ID_TRACKER_FILE, "r", encoding="utf-8") as f:
            return set(line.strip() for line in f)
    except Exception:
        return set()


def add_id_to_tracker(photo_id):
    """Yeni paylaşılan fotoğraf ID'sini dosyaya ekler."""
    try:
        with open(ID_TRACKER_FILE, "a", encoding="utf-8") as f:
            f.write(f"{photo_id}\n")
    except Exception as e:
        print(f"ID dosyasına yazma hatası: {e}")


# ----------------------------------------------------
# 4. PEXELS API
# ----------------------------------------------------
def fetch_unique_photo_data(shared_ids):
    """Pexels'ten daha önce paylaşılmamış rastgele bir fotoğraf çeker."""
    print("📷 Yeni fotoğraf aranıyor...")

    if not PEXELS_API_KEY:
        print("❌ PEXELS_API_KEY ayarlanmamış.")
        return None

    attempts = 0

    while attempts < 10:
        attempts += 1
        headers = {"Authorization": PEXELS_API_KEY}
        url = f"https://api.pexels.com/v1/curated?per_page=1&page={random.randint(1, 100)}"

        try:
            res = requests.get(url, headers=headers, timeout=15)
            res.raise_for_status()
            data = res.json()
        except requests.exceptions.RequestException as e:
            print(f"❌ Pexels API hatası, {attempts}. deneme → Bekleme: 10sn | {e}")
            time.sleep(10)
            continue

        if data.get("photos"):
            photo = data["photos"][0]
            photo_id_str = str(photo["id"])
            if photo_id_str not in shared_ids:
                print(f"✔️ Benzersiz fotoğraf bulundu: {photo_id_str}")
                return {
                    "id": photo_id_str,
                    "url_tiny": photo["src"]["tiny"],
                    "url_original": photo["src"]["original"],
                    "photographer": photo["photographer"],
                }
            else:
                print(f"↻ Tekrar eden ID ({photo_id_str}) → yeni arama...")
        else:
            print("⚠️ Pexels'ten fotoğraf gelmedi.")

        time.sleep(3)

    print("❌ 10 denemede benzersiz fotoğraf bulunamadı.")
    return None


def download_image(url, filename):
    """Belirtilen URL'den dosyayı indirir."""
    try:
        res = requests.get(url, stream=True, timeout=30)
        res.raise_for_status()

        with open(filename, "wb") as f:
            for chunk in res.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        return filename
    except requests.exceptions.RequestException as e:
        print(f"❌ Resim indirme hatası ({filename}): {e}")
        return None


# ----------------------------------------------------
# 5. GEMINI CAPTION ÜRETİCİ
# ----------------------------------------------------
def generate_ai_caption(photo_data, image_path):
    """
    Fotoğrafı Gemini'ye gönderip, X için kısa bir caption üretir.
    - Modeller: gemini-2.5-flash ve gemini-2.5-pro (sırayla)
    - Tüm tweet (caption + footer) max 250 karakter
    """

    def static_caption():
        """API başarısız olursa kullanılacak statik yedek metin."""
        return (
            f"STOP SCROLLING. Here is your moment of visual escape.\n"
            f"📌 Long press for 4K.\n"
            f"Photo by {photo_data['photographer']} #Inspiration"
        )[:250]

    if not GEMINI_CLIENT or not image_path or not os.path.exists(image_path):
        return static_caption()

    MODELS = ["gemini-2.5-flash", "gemini-2.5-pro"]
    FOOTER = f"\n\n📌 Save this.\n 📸 Long press for 4K.\n 📸 {photo_data['photographer']} #Inspiration"
    MAX_LEN = 250

    try:
        img_bytes = open(image_path, "rb").read()
    except Exception:
        return static_caption()

    for model in MODELS:
        print(f"🤖 Gemini modeli deneniyor: {model}")

        for attempt in range(1, 4):
            try:
                # PROMPT GÜNCELLENDİ: Farklı açılış cümleleri istendi.
                prompt = (
                    "Generate a single, short Twitter caption based on the image. "
                    "The caption must start with a **highly engaging opening phrase** that acts as a scroll-stopper (e.g., 'WAIT!', 'Moment of calm:', 'Take a deep breath:', 'Can't look away from this:', 'Viral potential:'). "
                    "Include **one thoughtful question** and **max 3 relevant hashtags**. "
                    "The entire generated text must be MAX 200 characters to leave space for the footer."
                )

                response = GEMINI_CLIENT.models.generate_content(
                    model=model,
                    contents=[
                        types.Part.from_bytes(
                            data=img_bytes,
                            mime_type="image/jpeg",
                        ),
                        prompt,
                    ],
                )

                caption = (response.text or "").strip()
                if not caption:
                    continue

                # Caption için kalacak alanı hesapla
                space_remaining = MAX_LEN - len(FOOTER)
                if space_remaining <= 0:
                    return static_caption()

                caption = caption[:space_remaining]

                final = (caption + FOOTER)[:MAX_LEN]

                print("✨ Üretilen Caption:", final)
                print(f"🧮 Karakter Sayısı: {len(final)}")
                return final

            except APIError as e:
                error_msg = str(e)
                if "429" in error_msg:
                    print("🛑 429 Rate Limit aşıldı → Statik caption.")
                    return static_caption()
                elif "503" in error_msg or "UNAVAILABLE" in error_msg:
                    print(f"⚠️ Model yoğun! {model} | Deneme {attempt}/3 → Bekleme: 15sn")
                    time.sleep(15)
                    continue
                else:
                    print(f"❌ Diğer API hatası ({model}): {error_msg}")
                    break
            except Exception as e:
                print(f"⚠️ Beklenmeyen Hata ({model}): {e}")
                break

    print("❌ Tüm modeller hata verdi → Statik mod aktif.")
    return static_caption()


# ----------------------------------------------------
# 6. ANA ÇALIŞTIRMA FONKSİYONU
# ----------------------------------------------------
def run_bot_task():

    print(f"\n🚀 BOT ÇALIŞTI — {time.strftime('%H:%M:%S')}")

    tiny_img_path = TEMP_GEMINI_IMAGE
    original_img_path = TEMP_TWITTER_IMAGE

    client, api_v1 = get_twitter_client()
    if not client or not api_v1:
        print("❌ Twitter bağlantısı kurulamadı. Görev iptal.")
        return

    shared = get_shared_ids()
    photo = fetch_unique_photo_data(shared)

    if not photo:
        print("❌ Benzersiz fotoğraf bulunamadı. Görev iptal.")
        return

    try:
        # Fotoğrafları indir
        tiny = download_image(photo["url_tiny"], tiny_img_path)
        original = download_image(photo["url_original"], original_img_path)

        if not tiny or not original:
            print("❌ Resim indirme başarısız. Görev iptal.")
            return

        # Caption üret
        caption = generate_ai_caption(photo, tiny)

        # Medya yükle (v1) ve Tweet at (v2)
        print("🔗 Medya yükleniyor...")
        media = api_v1.media_upload(filename=original)

        print("🐦 Tweet atılıyor...")
        tw = client.create_tweet(text=caption, media_ids=[media.media_id_string])

        # Başarılı olursa ID'yi kaydet
        add_id_to_tracker(photo["id"])

        print(f"🎉 Tweet atıldı! ID: {tw.data['id']}")

    except Exception as e:
        print(f"❌ Genel İşlem Hatası (Tweet/Medya): {e}")

    finally:
        # Geçici dosyaları sil
        if os.path.exists(tiny_img_path):
            os.remove(tiny_img_path)
        if os.path.exists(original_img_path):
            os.remove(original_img_path)
        print("🧹 Geçici dosyalar temizlendi.")


# ----------------------------------------------------
# 7. ZAMANLAYICI
# ----------------------------------------------------
if __name__ == "__main__":

    run_bot_task()

    #schedule.every(1).hours.do(run_bot_task)
    schedule.every(90).minutes.do(run_bot_task)
    print("\n🟢 BOT AKTİF — Otomatik paylaşıma hazır. (1.5 saat aralıklarla)\n")

    while True:
        schedule.run_pending()
        time.sleep(1)