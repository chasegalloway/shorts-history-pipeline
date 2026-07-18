"""Requeue failed topics so they get produced again. Usage: python requeue.py [title-substring]"""
import sys

from pipeline import db

con = db.connect()
if len(sys.argv) > 1:
    cur = con.execute(
        "UPDATE topics SET status='queued', used_at=NULL WHERE status='failed' AND title LIKE ?",
        (f"%{sys.argv[1]}%",))
else:
    cur = con.execute("UPDATE topics SET status='queued', used_at=NULL WHERE status='failed'")
con.commit()
print(f"requeued {cur.rowcount} topic(s)")
