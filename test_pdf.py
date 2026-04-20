from pypdf import PdfReader
import re

def test_extraction(pdf_path):
    reader = PdfReader(pdf_path)
    text = ""
    for page in reader.pages:
        text += page.extract_text() + "\n"
    
    print("--- ROHTEXT ANFANG ---")
    print(text)
    print("--- ROHTEXT ENDE ---")
    
    # Hier testen wir die verbesserte Logik
    job = "Nicht gefunden"
    company = "Nicht gefunden"
    
    # 1. Suche nach dem Job: Wir suchen zwischen "Arbeitsplatz als" oder "Bewerbung als" und dem Zeilenumbruch
    job_match = re.search(r'(?:Bewerbung als|Arbeitsplatz als)\s+([A-ZÄÖÜ][a-zäöüß]+(?:[\s-][A-ZÄÖÜ][a-zäöüß]+)*)', text, re.IGNORECASE)
    if job_match:
        job = job_match.group(1).strip()
    
    # 2. Suche nach der Firma: Wir suchen gezielt nach GmbH/AG/etc. am Ende einer Zeile
    # Wir nehmen nur den Teil der Zeile, der direkt vor der Rechtsform steht
    lines = text.split('\n')
    for line in lines:
        comp_match = re.search(r'([A-ZÄÖÜ0-9][A-Za-zÄÖÜäöüß0-9\&\.\-\s]+(?:\s(?:GmbH|AG|e\.V\.|OHG|KG|GmbH\s&\sCo\.\sKG)))', line)
        if comp_match:
            company = comp_match.group(1).strip()
            # Falls der Jobname mit in der Zeile klebt, schneiden wir ihn weg
            if job != "Nicht gefunden" and job in company:
                company = company.split(job)[-1].strip()
            break

    print(f"\nERGEBNIS:")
    print(f"Stelle: {job}")
    print(f"Firma:  {company}")

if __name__ == "__main__":
    test_extraction("Industriemechaniker - Max Mustermann GmbH.pdf")
