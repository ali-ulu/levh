"""WebSocket route — live activity feed plus a small RPC surface."""

from __future__ import annotations


from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from server.routes import deps
from server.routes.deps import get_engine
from server.routes.deps import constant_time_token_matches, public_demo

router = APIRouter()


@router.websocket("/ws/memory")
async def memory_websocket(ws: WebSocket):
    # Mirror the REST token gate: when a token is configured, the socket must
    # present it via the X-LEVH-Token header or a ?token= query param. The
    # legacy X-StackMemory-Token header remains accepted.
    if deps.api_token():
        supplied = (
            ws.headers.get("x-levh-token")
            or ws.headers.get("x-stackmemory-token")
            or ws.query_params.get("token")
            or ""
        )
        client_key = f"ws:{ws.client.host if ws.client else 'unknown'}"
        if not constant_time_token_matches(supplied, deps.api_token()):
            allowed, _ = deps.auth_limiter.allow(client_key)
            # 1008 = policy violation; 1013 asks a compliant client to retry
            # later once the rate window has elapsed.
            await ws.close(code=1008 if allowed else 1013)
            return
        allowed, _ = deps.api_limiter.allow(client_key)
        if not allowed:
            await ws.close(code=1013)
            return
    # Public demo mode: only allow read-only WebSocket actions
    if public_demo():
        await ws.accept()
        engine = await get_engine()
        deps.set_event_loop_if_unset()
        deps.ws_clients().add(ws)
        try:
            while True:
                data = await ws.receive_json()
                action = data.get("action")

                if action == "recall":
                    result = await engine.recall(**data.get("params", {}))
                    await ws.send_json({
                        "type": "recalled",
                        "results": [
                            {"memory": m.model_dump(exclude={"embedding"}), "score": s}
                            for m, s in zip(result.memories, result.scores)
                        ],
                    })

                elif action == "stats":
                    stats = await engine.get_stats()
                    await ws.send_json({"type": "stats", "stats": stats.model_dump()})

                elif action == "ping":
                    await ws.send_json({"type": "pong"})

                else:
                    await ws.send_json({"type": "error", "message": f"action '{action}' forbidden in public demo mode"})
        except WebSocketDisconnect:
            pass
        finally:
            deps.ws_clients().discard(ws)
        return

    await ws.accept()
    engine = await get_engine()
    deps.set_event_loop_if_unset()
    deps.ws_clients().add(ws)
    try:
        while True:
            data = await ws.receive_json()
            action = data.get("action")

            if action == "store":
                params = dict(data.get("params", {}))
                params.pop("force", None)  # WebSocket is not an admin bypass surface.
                result = await engine.admit_memory(**params)
                if result["stored"]:
                    await ws.send_json({"type": "stored", "memory": result["memory"]})
                else:
                    await ws.send_json({
                        "type": "admission_blocked",
                        "decision": result["decision"],
                    })

            elif action == "recall":
                result = await engine.recall(**data.get("params", {}))
                await ws.send_json({
                    "type": "recalled",
                    "results": [
                        {"memory": m.model_dump(exclude={"embedding"}), "score": s}
                        for m, s in zip(result.memories, result.scores)
                    ],
                })

            elif action == "forget":
                success = await engine.forget(data["params"]["memory_id"])
                await ws.send_json({
                    "type": "forgotten",
                    "memory_id": data["params"]["memory_id"],
                    "success": success,
                })

            elif action == "stats":
                stats = await engine.get_stats()
                await ws.send_json({"type": "stats", "stats": stats.model_dump()})

            elif action == "ping":
                await ws.send_json({"type": "pong"})

            else:
                await ws.send_json({"type": "error", "message": f"Unknown action: {action}"})

    except WebSocketDisconnect:
        pass
    finally:
        deps.ws_clients().discard(ws)
