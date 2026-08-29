# app/workers/celery_app.py
from celery import Celery
from celery.schedules import crontab
from app.core.config import settings

celery_app = Celery(
    "proxmox-iaas-backend",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "app.workers.task_scheduler",
        "app.workers.tasks.tenant",
        "app.workers.tasks.firewall_manager",
        "app.workers.tasks.images",
        "app.workers.tasks.wireguard",
    ]
)

celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_time_limit=3600,
    task_soft_time_limit=3300,
    worker_prefetch_multiplier=1,
    beat_schedule={
        'sync-all-wan-ips': {
            'task': 'tasks.sync_all_wan_ips',
            'schedule': crontab(minute='*/5'),
            'options': {'expires': 240},
        },
        'sync-opnsense-firewall-rules': {
            'task': 'tasks.sync_all_firewall_rules',
            'schedule': crontab(minute='*/15'),
            'options': {'expires': 300},
        },
    },
)
