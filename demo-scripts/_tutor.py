import asyncio, json, sys, urllib.request, uuid, websockets
B="http://127.0.0.1:8000"
def call(path, body=None, token=None):
    d = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(B+path, data=d, method="POST" if d is not None else "GET")
    if d is not None: r.add_header("Content-Type","application/json")
    if token: r.add_header("Authorization","Bearer "+token)
    try:
        with urllib.request.urlopen(r, timeout=60) as f: return f.status, json.loads(f.read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read())

async def main(code, question):
    s, cred = call("/v1/device-credentials", {"request_id": str(uuid.uuid4()), "access_code": code})
    tok = cred["credential"]
    s, sess = call("/v1/sessions", {}, token=tok); sid = sess["session_id"]
    print("session", sid, "version", sess.get("session_version"))
    s, srcs = call(f"/v1/sessions/{sid}/sources", token=tok)
    print("sources:", json.dumps(srcs)[:300])
    src = srcs["sources"][0]
    ver = src["active_source_version_id"]
    s, pin = call(f"/v1/sessions/{sid}/source",
                  {"request_id": str(uuid.uuid4()), "source_version_id": ver,
                   "expected_session_version": sess.get("session_version", 0)}, token=tok)
    print("pin source ->", s, json.dumps(pin)[:200])
    snap = pin.get("snapshot", {})
    ver_no = snap.get("session_version", 1)
    async with websockets.connect("ws://127.0.0.1:8000/v1/ws",
                                  additional_headers={"Authorization": "Bearer "+tok}) as ws:
        msg = {"protocol_version":"1.0","message_id":str(uuid.uuid4()),"session_id":sid,
               "request_id":str(uuid.uuid4()),"sequence":1,"type":"turn.submit",
               "payload":{"utterance":question,"input_mode":"keyboard","transcript_status":"final",
                          "expected_session_version":ver_no,
                          "client_timestamp":"2026-09-20T02:00:00Z"}}
        await ws.send(json.dumps(msg))
        print("--- asked:", question)
        try:
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=45)
                m = json.loads(raw) if isinstance(raw,str) else {"type":"binary","bytes":len(raw)}
                t = m.get("type")
                p = m.get("payload", {})
                text = p.get("text") or p.get("message") or ""
                print(f"  <- {t}: {str(text)[:220]}")
                if t in ("turn.completed","error","turn.failed"): break
        except asyncio.TimeoutError:
            print("  (timed out waiting for more frames)")

asyncio.run(main(sys.argv[1], sys.argv[2]))
