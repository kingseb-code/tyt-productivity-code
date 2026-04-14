"""
Run this once to authorise OpenClaw to read your Gmail.
It will open a browser window — approve access, then close it.
The token is saved to gmail_token.json and used automatically from then on.

Usage:
    cd ~/tyt-productivity-code
    .venv/bin/python3 scripts/auth_gmail.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from briefing import get_gmail_service

if __name__ == "__main__":
    print("Opening browser for Gmail authorisation...")
    service = get_gmail_service()
    profile = service.users().getProfile(userId="me").execute()
    print(f"\nSuccess! Connected to: {profile['emailAddress']}")
    print("gmail_token.json saved. You won't need to do this again.")
