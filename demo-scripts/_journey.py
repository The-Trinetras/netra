import json, sys, urllib.request, uuid
B="http://127.0.0.1:8000"
def call(path, body=None, token=None, raw=None, ctype="application/json"):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    r = urllib.request.Request(B+path, data=data, method="POST" if data is not None else "GET")
    if data is not None: r.add_header("Content-Type", ctype)
    if token: r.add_header("Authorization", "Bearer "+token)
    try:
        with urllib.request.urlopen(r, timeout=30) as f: return f.status, json.loads(f.read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read())

s, cred = call("/v1/device-credentials", {"request_id": str(uuid.uuid4()), "access_code": sys.argv[1]})
print("1. exchange access code ->", s)
if s != 200 and s != 201: print(json.dumps(cred)[:400]); sys.exit(1)
tok = cred["credential"]
print("   credential received:", bool(tok))
s, sess = call("/v1/sessions", {}, token=tok)
print("2. create session ->", s, json.dumps(sess)[:200] if s>=400 else "")
sid = (sess.get("session") or sess).get("session_id") if s < 400 else None
print("   session_id:", sid)

import mimetypes, time, pathlib
pdf = pathlib.Path("docs/One Dinner Four Kitchens.pdf")
data = pdf.read_bytes()
boundary = "----netra" + uuid.uuid4().hex
def part(name, value, filename=None, ctype=None):
    h = f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"'
    if filename: h += f'; filename="{filename}"'
    h += "\r\n"
    if ctype: h += f"Content-Type: {ctype}\r\n"
    return h.encode() + b"\r\n" + (value if isinstance(value, bytes) else value.encode()) + b"\r\n"
rid = str(uuid.uuid4())
body = (part("request_id", rid) + part("title", "One Dinner, Four Kitchens")
        + part("file", data, pdf.name, "application/pdf") + f"--{boundary}--\r\n".encode())
s, up = call(f"/v1/sessions/{sid}/uploads", raw=body, token=tok,
             ctype=f"multipart/form-data; boundary={boundary}")
print(f"3. upload {pdf.name} ({len(data)} bytes) ->", s)
print("  ", json.dumps(up)[:300])
if s >= 400: sys.exit(1)
job = up["job"]["job_id"]
s2, rep = call(f"/v1/sessions/{sid}/uploads", raw=body, token=tok,
               ctype=f"multipart/form-data; boundary={boundary}")
print("4. retransmit same request_id ->", s2, "same job:", rep["job"]["job_id"] == job)
for i in range(20):
    s3, j = call(f"/v1/sessions/{sid}/jobs/{job}", token=tok)
    st = j["job"]["state"]
    print(f"5.{i} poll -> {s3} state={st}")
    if st != "processing": break
    time.sleep(3)

s4, srcs = call(f"/v1/sessions/{sid}/sources", token=tok)
print("6. sources ->", s4, "count:", len(srcs.get("sources", [])))
for x in srcs.get("sources", [])[:5]:
    print("   ", x.get("status","?"), "|", str(x.get("title"))[:50])
