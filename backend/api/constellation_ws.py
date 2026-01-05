"""WebSocket endpoint for real-time Constellation updates"""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from typing import Set
import json
import asyncio

router = APIRouter(prefix="/constellation", tags=["constellation"])

# Connected clients
connected_clients: Set[WebSocket] = set()


async def broadcast_update(event_type: str, data: dict):
    """Broadcast an update to all connected clients"""
    if not connected_clients:
        return
    
    message = json.dumps({
        "type": event_type,
        "data": data
    })
    
    disconnected = set()
    for client in connected_clients:
        try:
            await client.send_text(message)
        except Exception:
            disconnected.add(client)
    
    # Clean up disconnected clients
    for client in disconnected:
        connected_clients.discard(client)


async def notify_concept_added(concept: dict):
    """Notify clients when a new concept is added"""
    await broadcast_update("concept_added", concept)


async def notify_link_added(link: dict):
    """Notify clients when a new link is added"""
    await broadcast_update("link_added", link)


async def notify_cluster_updated(cluster: dict):
    """Notify clients when clusters are updated"""
    await broadcast_update("cluster_updated", cluster)


async def notify_build_progress(progress: dict):
    """Notify clients of build progress"""
    await broadcast_update("build_progress", progress)


async def notify_build_complete():
    """Notify clients when build is complete"""
    await broadcast_update("build_complete", {})


async def notify_source_updated(source: dict):
    """Notify clients when a source's status changes (e.g., processing -> completed)"""
    await broadcast_update("source_updated", source)


async def notify_cluster_progress(progress: dict):
    """Notify clients of clustering progress"""
    await broadcast_update("cluster_progress", progress)


async def notify_cluster_complete(stats: dict = None):
    """Notify clients when clustering is complete - triggers theme refresh"""
    await broadcast_update("cluster_complete", stats or {})


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time constellation updates"""
    await websocket.accept()
    connected_clients.add(websocket)
    
    try:
        # Send initial connection confirmation
        await websocket.send_json({
            "type": "connected",
            "data": {"message": "Connected to Constellation updates"}
        })
        
        # Keep connection alive and handle incoming messages
        while True:
            try:
                # Wait for messages (ping/pong or commands)
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30)
                
                # Handle ping
                if data == "ping":
                    await websocket.send_text("pong")
                    
            except asyncio.TimeoutError:
                # Send heartbeat
                try:
                    await websocket.send_json({"type": "heartbeat"})
                except Exception:
                    break
                    
    except WebSocketDisconnect:
        pass
    finally:
        connected_clients.discard(websocket)
