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
                    # Entferne Leerzeichen und Anführungszeichen
                    os.environ[key.strip()] = value.strip().strip('"').strip("'")

load_dotenv()
API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

def list_models():
    print(f"Frage verfügbare Modelle ab (Key: {API_KEY[:4]}...{API_KEY[-4:]})")
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={API_KEY}"
    
    try:
        with urlopen(Request(url, method="GET"), timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            models = [m["name"].replace("models/", "") for m in data.get("models", [])]
            print("\nErfolg! Folgende Modelle sind für dich verfügbar:")
            for m in models:
                print(f"- {m}")
            return models
    except HTTPError as e:
        error_text = e.read().decode("utf-8")
        print(f"\nFEHLER ({e.code}): {error_text}")
        return []
    except Exception as e:
        print(f"\nVerbindungsfehler: {e}")
        return []

if __name__ == "__main__":
    if not API_KEY:
        print("Kein API-Key in .env gefunden!")
    else:
        list_models()
