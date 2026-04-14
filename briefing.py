import os
import json
import logging
from datetime import datetime, timedelta, timezone

import anthropic
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
CREDENTIALS_FILE = os.getenv("GMAIL_CREDENTIALS_FILE", "gmail_credentials.json")
TOKEN_FILE = os.getenv("GMAIL_TOKEN_FILE", "gmail_token.json")


def get_gmail_service():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return build("gmail", "v1", credentials=creds)


def fetch_recent_emails(service, hours=24):
    after = int((datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp())
    query = f"after:{after} -category:promotions -category:social -from:noreply -from:no-reply"
    result = service.users().messages().list(userId="me", q=query, maxResults=30).execute()
    messages = result.get("messages", [])

    emails = []
    for msg in messages:
        data = service.users().messages().get(
            userId="me", id=msg["id"], format="metadata",
            metadataHeaders=["From", "Subject", "Date"]
        ).execute()

        headers = {h["name"]: h["value"] for h in data["payload"]["headers"]}
        snippet = data.get("snippet", "")

        emails.append({
            "from": headers.get("From", "Unknown"),
            "subject": headers.get("Subject", "(no subject)"),
            "date": headers.get("Date", ""),
            "snippet": snippet[:300],
        })

    return emails


def generate_briefing(emails: list) -> str:
    if not emails:
        return "No new emails requiring attention in the last 24 hours."

    client = anthropic.Anthropic(api_key=os.getenv("CLAUDE_API_KEY"))

    email_text = "\n\n".join([
        f"From: {e['from']}\nSubject: {e['subject']}\nDate: {e['date']}\nSnippet: {e['snippet']}"
        for e in emails
    ])

    prompt = f"""You are an assistant for a business owner. Review these emails from the last 24 hours and produce a concise daily briefing.

Organize your response into these sections (only include sections that have content):
1. URGENT — needs action today
2. FOLLOW-UPS — waiting on someone or needs a reply
3. ACTION NEEDED — tasks or requests to address

Be specific: name the sender and topic. Skip newsletters, automated notifications, and anything that needs no action.
If nothing needs attention, say so briefly.

Emails:
{email_text}"""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    return message.content[0].text


def run_briefing() -> str:
    try:
        service = get_gmail_service()
        emails = fetch_recent_emails(service)
        briefing = generate_briefing(emails)
        now = datetime.now().strftime("%a, %d %b %Y")
        return f"*Daily Briefing — {now}*\n\n{briefing}"
    except Exception as e:
        logger.error(f"Briefing failed: {e}")
        return f"Briefing error: {e}"
