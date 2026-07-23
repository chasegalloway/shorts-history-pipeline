"""One-shot TikTok consent flow: opens a browser, caches secrets/tiktok_token.json.

Run this once (and again only if the 365-day refresh token expires or scopes
change) so the unattended overnight run never needs a browser.

Usage: python tiktok_auth.py
"""
from pipeline.tiktok import authorize

authorize()
print("Authorized with scopes: video.publish + user.info.basic")
