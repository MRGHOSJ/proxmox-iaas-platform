import asyncio
import json
import logging
import os
from typing import Dict, List, Any, Optional, Union
from fastapi import WebSocket
import redis

from redis.asyncio import from_url

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
REDIS_CHANNEL = "cloud:status_updates"

_sync_redis_client = None


def get_sync_redis():
    global _sync_redis_client
    if _sync_redis_client is None:
        _sync_redis_client = redis.from_url(REDIS_URL)
    return _sync_redis_client


class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[int | str, List[WebSocket]] = {}
        self._redis_sub_task = None

    async def connect(self, websocket: WebSocket, key: Optional[Union[int, str]] = None):
        await websocket.accept()

        conn_key = key if key is not None else "global"

        if conn_key not in self.active_connections:
            self.active_connections[conn_key] = []
        self.active_connections[conn_key].append(websocket)

        logger.info(f"WebSocket client connected for {conn_key}. Total: {len(self.active_connections[conn_key])}")

    def disconnect(self, websocket: WebSocket, key: Optional[Union[int, str]] = None):
        conn_key = key if key is not None else "global"

        if conn_key in self.active_connections:
            if websocket in self.active_connections[conn_key]:
                self.active_connections[conn_key].remove(websocket)
            if not self.active_connections[conn_key]:
                del self.active_connections[conn_key]
        logger.info(f"WebSocket client disconnected from {conn_key}")

    async def broadcast(self, message: Dict[str, Any], key: Optional[Union[int, str]] = None):
        conn_key = key if key is not None else "global"
        target_connections = self.active_connections.get(conn_key, [])

        if not target_connections:
            return

        message_json = json.dumps(message)
        disconnected = []

        for connection in target_connections:
            try:
                await connection.send_text(message_json)
            except Exception as e:
                logger.warning(f"Failed to send WebSocket message: {e}")
                disconnected.append(connection)

        for connection in disconnected:
            self.disconnect(connection, key)

    async def _start_redis_subscriber(self):
        try:
            r = await from_url(REDIS_URL, decode_responses=True)

            pubsub = r.pubsub()
            await pubsub.subscribe(REDIS_CHANNEL)

            await pubsub.psubscribe("cloud:network:*:logs")
            await pubsub.psubscribe("cloud:network-create:*:logs")
            await pubsub.psubscribe("cloud:vm:*:logs")
            await pubsub.psubscribe("cloud:tenant:*:logs")
            await pubsub.psubscribe("cloud:wireguard:*:logs")
            await pubsub.psubscribe("cloud:wireguard-peer:*:logs")

            logger.info(f"Redis subscriber connected to {REDIS_URL}")

            async for msg in pubsub.listen():
                if msg["type"] == "message" or msg["type"] == "pmessage":
                    await self._handle_redis_message(msg["data"])

        except asyncio.CancelledError:
            logger.info("Redis subscriber task cancelled.")
        except Exception as e:
            logger.error(f"Failed to start Redis subscriber: {e}")

    async def _handle_redis_message(self, data_str: str):
        try:
            data = json.loads(data_str)
            resource_id = data.get("resource_id")
            resource_type = data.get("resource_type", "")

            if data.get("type") in ("log", "step_update") and resource_id is not None:
                if resource_type == "tenant":
                    await self.broadcast(data, f"tenant:{resource_id}")
                elif resource_type == "network-create":
                    await self.broadcast(data, resource_id)
                elif resource_type == "vm":
                    await self.broadcast(data, resource_id)
                elif resource_type == "wireguard":
                    await self.broadcast(data, f"wireguard:{resource_id}")
                    await self.broadcast(data)
                elif resource_type == "wireguard_peer":
                    await self.broadcast(data, f"wireguard-peer:{resource_id}")
                    await self.broadcast(data)
                else:
                    await self.broadcast(data, resource_id)
            elif data.get("type") == "status_change":
                if resource_type == "tenant":
                    await self.broadcast(data, f"tenant:{resource_id}")
                elif resource_type == "firewall":
                    await self.broadcast(data, f"tenant:{resource_id}")
                elif resource_type == "wireguard_tunnel":
                    await self.broadcast(data, f"wireguard:{resource_id}")
                    await self.broadcast(data)
                elif resource_type == "wireguard_peer":
                    await self.broadcast(data, f"wireguard-peer:{resource_id}")
                    await self.broadcast(data)
                else:
                    await self.broadcast(data)
            else:
                await self.broadcast(data)
        except Exception as e:
            logger.error(f"Error processing Redis message: {e}")

    def start_redis_listener(self):
        if self._redis_sub_task is None or self._redis_sub_task.done():
            self._redis_sub_task = asyncio.create_task(self._start_redis_subscriber())
            logger.info("Started Redis status update listener")


manager = ConnectionManager()


def publish_status_update(
    resource_type: str,
    resource_id: int,
    old_status: str,
    new_status: str,
    additional_data: Dict[str, Any] = None,
):
    message = {
        "type": "status_change",
        "resource_type": resource_type,
        "resource_id": resource_id,
        "old_status": old_status,
        "new_status": new_status,
    }
    if additional_data:
        safe_data = {k: v for k, v in additional_data.items() if k != "type"}
        message.update(safe_data)

    try:
        r = get_sync_redis()
        r.publish(REDIS_CHANNEL, json.dumps(message))
        logger.info(f"Published status update: {message}")
    except Exception as e:
        logger.error(f"Failed to publish status update: {e}")


def publish_log_update(network_id: int, message: str):
    channel = f"cloud:network-create:{network_id}:logs"
    payload = {
        "type": "log",
        "resource_type": "network-create",
        "resource_id": network_id,
        "message": message,
    }
    try:
        r = get_sync_redis()
        r.publish(channel, json.dumps(payload))
    except Exception as e:
        logger.error(f"Failed to publish network log: {e}")


def publish_vm_log_update(vm_id: int, message: str):
    channel = f"cloud:vm:{vm_id}:logs"
    payload = {
        "type": "log",
        "resource_type": "vm",
        "resource_id": vm_id,
        "message": message,
    }
    try:
        r = get_sync_redis()
        r.publish(channel, json.dumps(payload))
    except Exception as e:
        logger.error(f"Failed to publish VM log: {e}")


def publish_tenant_log_update(tenant_id: int, message: str, level: str = "info"):
    channel = f"cloud:tenant:{tenant_id}:logs"
    payload = {
        "type": "log",
        "resource_type": "tenant",
        "resource_id": tenant_id,
        "level": level,
        "message": message,
    }
    try:
        r = get_sync_redis()
        r.publish(channel, json.dumps(payload))
    except Exception as e:
        logger.error(f"Failed to publish tenant log: {e}")


def publish_wireguard_log_update(tunnel_id: int, message: str, level: str = "info"):
    channel = f"cloud:wireguard:{tunnel_id}:logs"
    payload = {
        "type": "log",
        "resource_type": "wireguard",
        "resource_id": tunnel_id,
        "level": level,
        "message": message,
    }
    try:
        r = get_sync_redis()
        r.publish(channel, json.dumps(payload))
    except Exception as e:
        logger.error(f"Failed to publish wireguard log: {e}")


def publish_wireguard_tunnel_step(
    tunnel_id: int,
    step: int,
    total_steps: int,
    message: str,
    status: str = "done",
):
    channel = f"cloud:wireguard:{tunnel_id}:logs"
    payload = {
        "type": "step_update",
        "resource_type": "wireguard",
        "resource_id": tunnel_id,
        "step": step,
        "total_steps": total_steps,
        "message": message,
        "status": status,
    }
    try:
        r = get_sync_redis()
        r.publish(channel, json.dumps(payload))
    except Exception as e:
        logger.error(f"Failed to publish wireguard tunnel step: {e}")


def publish_wireguard_peer_step(
    peer_id: int,
    step: int,
    total_steps: int,
    message: str,
    status: str = "done",
):
    channel = f"cloud:wireguard-peer:{peer_id}:logs"
    payload = {
        "type": "step_update",
        "resource_type": "wireguard_peer",
        "resource_id": peer_id,
        "step": step,
        "total_steps": total_steps,
        "message": message,
        "status": status,
    }
    try:
        r = get_sync_redis()
        r.publish(channel, json.dumps(payload))
    except Exception as e:
        logger.error(f"Failed to publish wireguard peer step: {e}")


async def broadcast_status_change(
    resource_type: str,
    resource_id: int,
    old_status: str,
    new_status: str,
    additional_data: Dict[str, Any] = None,
):
    message = {
        "type": "status_change",
        "resource_type": resource_type,
        "resource_id": resource_id,
        "old_status": old_status,
        "new_status": new_status,
    }
    if additional_data:
        message.update(additional_data)

    await manager.broadcast(message)