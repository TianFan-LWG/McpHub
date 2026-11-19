"""极简 stdio MCP：只有一个 ping 工具，用于验证 WorkBuddy 是否支持 stdio 类型 MCP。

零依赖、手写 JSON-RPC，握手极快，排除冷启动与复杂逻辑干扰。
"""

import json
import sys


TOOLS = [
    {
        "name": "ping",
        "description": "返回 pong，用于测试 stdio MCP 是否连通。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    }
]


def send(payload):
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def handle(msg):
    req_id = msg.get("id")
    method = msg.get("method", "")

    if method == "initialize":
        version = (msg.get("params") or {}).get("protocolVersion", "2024-11-05")
        send({
            "jsonrpc": "2.0", "id": req_id,
            "result": {
                "protocolVersion": version,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "echo-stdio-test", "version": "1.0.0"},
            },
        })
        return
    if method.startswith("notifications/"):
        return
    if method == "tools/list":
        send({"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}})
        return
    if method == "tools/call":
        name = (msg.get("params") or {}).get("name", "")
        if name == "ping":
            send({
                "jsonrpc": "2.0", "id": req_id,
                "result": {"content": [{"type": "text", "text": "pong"}]},
            })
        else:
            send({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"unknown tool: {name}"}})
        return
    if method == "ping":
        send({"jsonrpc": "2.0", "id": req_id, "result": {}})
        return
    send({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"unknown method: {method}"}})


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    for line in sys.stdin.buffer:
        if not line.strip():
            continue
        try:
            msg = json.loads(line.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
            continue
        if isinstance(msg, dict):
            handle(msg)


if __name__ == "__main__":
    main()
