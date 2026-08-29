# WebSocket Documentation

This document describes the WebSocket system for real-time updates.

---

## Table of Contents
- [Overview](#overview)
- [Configuration](#configuration)
- [Connection Manager](#connection-manager)
- [Usage](#usage)
- [Message Format](#message-format)

---

## Overview

**File:** `app/core/websocket.py`

The WebSocket system provides real-time updates for:
- VM status changes
- Task completion notifications
- Network topology updates
- Tenant provisioning progress

---

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CELERY_BROKER_URL` | `redis://localhost:6379/0` | Redis URL |

### Redis Channel

| Channel | Description |
|---------|-------------|
| `cloud:status_updates` | Global status broadcast channel |

---

## Connection Manager

**Class:** `ConnectionManager`

### Methods

| Method | Description |
|--------|-------------|
| `connect(websocket, network_id)` | Accept WebSocket connection |
| `disconnect(websocket, network_id)` | Remove connection |
| `broadcast(message, network_id)` | Send to specific network |
| `broadcast_global(message)` | Send to all connections |

### Connection Types

- **Global:** `network_id=None` - All clients
- **Per-network:** `network_id=N` - Specific tenant network

---

## Usage

### Connecting

```python
from fastapi import WebSocket

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket_manager.connect(websocket)
    
    try:
        while True:
            data = await websocket.receive_text()
            # Handle messages
    except:
        websocket_manager.disconnect(websocket)
```

### Server-Side Broadcast

```python
from app.core.websocket import websocket_manager

# Broadcast to specific network
await websocket_manager.broadcast(
    {"type": "vm_status", "vm_id": 1, "status": "running"},
    network_id=1
)

# Broadcast to all
await websocket_manager.broadcast_global(
    {"type": "tenant_status", "tenant_id": 1, "status": "active"}
)
```

### Publishing from Tasks

```python
import redis
from app.core.websocket import get_sync_redis

def publish_status(vm_id, status):
    r = get_sync_redis()
    r.publish("cloud:status_updates", json.dumps({
        "type": "vm_status",
        "vm_id": vm_id,
        "status": status
    }))
```

---

## Message Format

### VM Status Update

```json
{
  "type": "vm_status",
  "vm_id": 1,
  "name": "web-server-01",
  "status": "running",
  "timestamp": "2026-04-23T12:00:00Z"
}
```

### Task Completion

```json
{
  "type": "task_complete",
  "task_id": "xxxx",
  "result": "success",
  "vm_id": 1
}
```

### Tenant Status

```json
{
  "type": "tenant_status",
  "tenant_id": 1,
  "status": "provisioning",
  "progress": 50
}
```

### Network Update

```json
{
  "type": "network_update",
  "network_id": 1,
  "vm_count": 5,
  "ips_used": 3
}
```

---

## Redis Integration

The system uses Redis Pub/Sub for cross-node broadcasting:

```
┌─────────────┐     publish      ┌─────────────┐
│   Celery    │ ──────────────► │    Redis    │
│   Worker   │                 │   Pub/Sub   │
└─────────────┘                 └──────┬──────┘
                                        │
                                   subscribe
                                        │
                                 ┌──────▼──────┐
                                 │  WebSocket  │
                                 │  Server    │
                                 └────────────┘
                                        │
                                   broadcast
                                        
                                 ┌──────▼──────┐
                                 │  Browser   │
                                 │  Client   │
                                 └────────────┘
```

### Redis Message Flow

1. **Celery task** publishes to Redis channel
2. **WebSocket server** subscribes to channel
3. **Server** broadcasts to connected WebSocket clients

---

## Client Example

```javascript
const ws = new WebSocket('wss://api.example.com/ws');

ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    
    if (data.type === 'vm_status') {
        updateVMStatus(data.vm_id, data.status);
    }
};
```

---

## Error Handling

| Error | Handling |
|-------|----------|
| WebSocket connection failed | Log warning, remove from active list |
| Message send failed | Remove disconnected client |
| Redis unavailable | Fallback to direct connections only |