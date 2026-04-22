import os
import json
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from pathlib import Path

def load_dotenv():
    env_path = Path(".env")
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, value = line.split("=", 1)
                    os.environ[key.strip()] = value.strip()

load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
MODELS = [
    "gemini-1.5-pro-latest", 
    "gemini-1.5-pro", 
    "gemini-1.5-flash-latest", 
    "gemini-1.5-flash", 
    "gemini-1.0-pro-latest",
    "gemini-1.0-pro"
]

def test_model(model_name):
    print(f"Teste Modell: {model_name} ...", end=" ", flush=True)
    url = f"https://generativelanguage.googleapis.com/v1/models/{model_name}:generateContent?key={API_KEY}"
    body = {"contents": [{"parts": [{"text": "Hallo, wer bist du? Antworte kurz."}]}]}
    
    req = Request(url, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
    
    try:
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            print("OK!")
            return True
    except HTTPError as e:
        print(f"FEHLER ({e.code})")
        return False
    except Exception as e:
        print(f"FEHLER: {e}")
        return False

if __name__ == "__main__":
    if not API_KEY:
        print("Fehler: Kein GEMINI_API_KEY in der .env gefunden!")
    else:
        print(f"Prüfe Modelle für Key (Länge {len(API_KEY)})...")
        results = {}
        for m in MODELS:
            results[m] = test_model(m)
        
        print("\nZusammenfassung:")
        for m, success in results.items():
            status = "VERFÜGBAR" if success else "NICHT VERFÜGBAR"
            print(f"- {m}: {status}")
