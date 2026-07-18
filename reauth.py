"""Re-run the OAuth consent flow to pick up new scopes (deletes cached token)."""
from pathlib import Path

from pipeline.upload import SECRETS, get_credentials

(SECRETS / "token.json").unlink(missing_ok=True)
get_credentials()
print("Re-authorized with scopes: upload + analytics.readonly")
