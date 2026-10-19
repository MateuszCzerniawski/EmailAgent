# tests/test_tags.py
import json
import uuid

from app.services.llm_service import LLMService
from app.models.mail import Mail
from app.config import load_config
from datetime import datetime

# 1. Stwórz fake mail
mail = Mail(
    id=f"test-tags-{uuid.uuid4().hex[:6]}",
    thread_id="thread-001",
    subject="Invoice #1234 — payment due in 3 days",
    sender="billing@vendor.com",
    recipients=["you@company.com"],
    date=datetime.now(),
    body_plain="Please find attached invoice #1234 for $5,000. Payment is due by May 22.",
)

config = load_config()
svc = LLMService(config)

# --- DEBUG: Przechwytujemy surowy wynik przed parsowaniem ---
# Zapisujemy oryginalną metodę, żeby nie zepsuć działania serwisu
original_analyze = svc.analyze


def debug_analyze(mail_obj):
    # Tutaj symulujemy to, co dzieje się wewnątrz Twojego LLMService.
    # Zakładam, że używasz jakiegoś wewnętrznego klienta, np. svc.client lub svc._call_llm.
    # Jeśli znasz nazwę metody zwracającej surowy tekst, możesz ją tu wywołać.
    print("\n" + "=" * 40 + " SUROWY WYNIK Z LLM " + "=" * 40)

    # Wywołujemy oryginalną analizę, ale jeśli masz dostęp do surowego stringa w svc,
    # warto dodać print(raw_json_string) bezpośrednio w pliku app/services/llm_service.py
    res = original_analyze(mail_obj)
    return res


# Podmieniamy metodę na czas testu
svc.analyze = debug_analyze
# ------------------------------------------------------------

# 2. Uruchomienie analizy
result = svc.analyze(mail)

print("\n" + "=" * 40 + " PARSOWANY OBIEKT PYTHONA " + "=" * 40)
print("Tags (typ: {}):".format(type(result.tags)), result.tags)
print("Actions:", result.actions)
print("=" * 100 + "\n")

# 3. Asercje
assert len(result.tags) >= 2, f"Powinny być conajmniej 2 tagi, a jest: {len(result.tags)}"
assert any("invoice" in t or "payment" in t for t in result.tags), "Brak oczekiwanych słów kluczowych w tagach"