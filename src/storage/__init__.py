"""Data storage modules"""

# FileStorage/JSONStorage/SQLiteStorage are data-pipeline only — not imported here
# to avoid pulling in chardet and other crawler dependencies on the web/Vercel path.
from .admission_store import AdmissionStore

__all__ = ["AdmissionStore"]
