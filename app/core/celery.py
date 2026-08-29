"""
DEPRECATED: This file is deprecated.

Please use app.workers.celery_app instead:

    from app.workers.celery_app import celery_app

This file is kept for backward compatibility and will be removed in a future version.
"""
import warnings
from app.workers.celery_app import celery_app

warnings.warn(
    "app.core.celery is deprecated. Use app.workers.celery_app instead.",
    DeprecationWarning,
    stacklevel=2
)
