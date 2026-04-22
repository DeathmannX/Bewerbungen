import os
import json
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from pathlib import Path

def load_dotenv():
    env_path = Path(".env")
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"): continue
                if "=" in line:
                    key, value = line.split("=", 1)
                    os.environ[key.strip()] = value.strip().strip('"').strip("'")

load_dotenv()
API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

def test_post():
    print(f"Teste POST an Modell: {MODEL}...")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={API_KEY}"
    body = {"contents": [{"parts": [{"text": "Schreibe eine Zeile Testtext."}]}]}
    
    req = Request(url, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
    
    try:
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            print("\nERFOLG! Antwort von Google:")
            print(json.dumps(data, indent=2))
    except HTTPError as e:
        error_text = e.read().decode("utf-8")
        print(f"\nFEHLER von Google ({e.code}):")
        print(error_text)
    except Exception as e:
        print(f"\nVerbindungsfehler: {e}")

if __name__ == "__main__":
    if not API_KEY:
        print("Kein API-Key!")
    else:
        test_post()
