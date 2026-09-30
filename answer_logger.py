# 答えの記録係（診断 Ver01.04・2026-09-27 試作）
# 診断の各軸の台本とモデル（llama-server）の間に挟む中継。やり取りはそのまま通し、
# 1件ごとに「終わった理由(finish_reason)・答えの文字数・トークン数・問題文の指紋」を jsonl に書き残す。
# 台本は1行も変えない（向き先のポート番号だけこちらに向ける）。長すぎ・打ち切りの補正の材料になる。
#   python answer_logger.py --listen 8700 --target 8081 --log answers_<label>.jsonl
import argparse, hashlib, json, threading, time, urllib.error, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ap = argparse.ArgumentParser()
ap.add_argument("--listen", type=int, default=8700); ap.add_argument("--target", type=int, required=True)
ap.add_argument("--host", default="127.0.0.1"); ap.add_argument("--log", required=True)
a = ap.parse_args()
LOCK = threading.Lock()


def prompt_key(body: dict) -> str:
    """問題文の指紋（同じ問題はどのモデルでも同じ値）。messages の中身だけから作る。"""
    msgs = body.get("messages") or [{"content": body.get("prompt", "")}]
    txt = "\n".join(str(m.get("content", "")) for m in msgs)
    return hashlib.sha1(txt.encode("utf-8")).hexdigest()[:16]


class H(BaseHTTPRequestHandler):
    def _pass(self, method):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else None
        url = f"http://{a.host}:{a.target}{self.path}"
        req = urllib.request.Request(url, data=raw, method=method,
                                     headers={k: v for k, v in self.headers.items() if k.lower() not in ("host", "content-length")})
        t0 = time.time()
        try:
            r = urllib.request.urlopen(req, timeout=3600); code = r.status; data = r.read(); hdr = r.headers
        except urllib.error.HTTPError as e:
            code, data, hdr = e.code, e.read(), e.headers
        self.send_response(code)
        for k, v in hdr.items():
            if k.lower() not in ("transfer-encoding", "connection", "content-length"):
                self.send_header(k, v)
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
        if method == "POST" and raw and self.path.rstrip("/").endswith(("chat/completions", "/completion", "/completions")):
            try:
                body = json.loads(raw); j = json.loads(data)
                if "choices" in j:
                    c = j["choices"][0]; msg = c.get("message") or {}
                    text = msg.get("content") or c.get("text") or ""
                    fin = c.get("finish_reason")
                else:   # llama.cpp の /completion
                    text = j.get("content", ""); fin = "length" if j.get("stop_type") == "limit" else "stop"
                rec = {"t": round(t0, 3), "path": self.path, "key": prompt_key(body), "finish_reason": fin,
                       "chars": len(text), "tokens": (j.get("usage") or {}).get("completion_tokens") or j.get("tokens_predicted"),
                       "max_tokens": body.get("max_tokens") or body.get("n_predict"), "sec": round(time.time() - t0, 2)}
                with LOCK, open(a.log, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            except Exception:
                pass

    def do_GET(self): self._pass("GET")
    def do_POST(self): self._pass("POST")
    def log_message(self, *x): pass


ThreadingHTTPServer(("127.0.0.1", a.listen), H).serve_forever()