#!/bin/bash
set -e

CONCURRENCY=${CELERY_CONCURRENCY:-2}

exec python -m celery -A app.workers.celery_app worker --loglevel=info -B --concurrency=$CONCURRENCY
