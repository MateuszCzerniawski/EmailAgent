# tests/test_tags.py
import json
from app.services.llm_service import LLMService
from app.models.mail import Mail
from app.config import load_config
from datetime import datetime

# Stwórz fake mail
mail = Mail(
    id="test-003",
    thread_id="thread-001",
    subject="Invoice #1234 — payment due in 3 days",
    sender="billing@vendor.com",
    recipients=["you@company.com"],
    date=datetime.now(),
    body_plain="Please find attached invoice #1234 for $5,000. Payment is due by May 22.",
)

config = load_config()
svc = LLMService(config)
result = svc.analyze(mail)
print("Tags:", result.tags)
print("Actions:", result.actions)
assert len(result.tags) >= 2, "Powinny być conajmniej 2 tagi"
assert any("invoice" in t or "payment" in t for t in result.tags)