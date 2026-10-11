"""Reprises courtes, sans bloquer les nouvelles preuves jusqu'au lendemain."""
import re
from datetime import datetime, timedelta, timezone

RETRY_MINUTES = 60

def next_retry(now=None, report=''):
    # Une première reprise après une heure, puis une fois par jour sans preuve nouvelle.
    minutes = 24 * 60 if re.search(r'(?:Recherche (?:album|visuelle) suivante|Prochaine recherche automatique) :', report) else RETRY_MINUTES
    return ((now or datetime.now(timezone.utc)) + timedelta(minutes=minutes)).isoformat(timespec='seconds')

def retry_due(report, label, now=None):
    matches = re.findall(re.escape(label) + r' : (\S+)', report)
    if not matches:
        return True
    value = matches[-1]
    # Legacy day-only deadlines are abandoned immediately after the repair.
    if len(value) == 10:
        return True
    try:
        deadline = datetime.fromisoformat(value)
        if deadline.tzinfo is None:
            return True
        return deadline <= (now or datetime.now(timezone.utc))
    except ValueError:
        return True
