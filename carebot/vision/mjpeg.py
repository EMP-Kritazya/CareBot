"""Live view in a browser for a headless robot: http://carebot.local:8000

By default the robot serves the **skeleton view** (stick figures on black), so even on the local
network nobody sees the room. The camera view (`--view camera`) is for setting the robot up.

    /            a page with the live view
    /stream      MJPEG stream
    /snapshot    one JPEG
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAGE = b"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CareBot live view</title><style>body{margin:0;background:#111;color:#ddd;font:14px system-ui}
img{display:block;max-width:100%;height:auto;margin:0 auto}p{text-align:center;opacity:.7}</style></head>
<body><img src="/stream" alt="CareBot live view"><p>CareBot live view. Video stays on the robot; this page
shows the skeleton view unless the robot was started with --view camera.</p></body></html>"""


class MjpegServer:
    def __init__(self, port: int = 8000, host: str = "0.0.0.0", quality: int = 70) -> None:
        import cv2

        self._cv2 = cv2
        self.quality = quality
        self._jpeg: bytes | None = None
        self._cond = threading.Condition()
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # quiet
                pass

            def do_GET(self):
                if self.path in ("/", "/index.html"):
                    self._send(200, "text/html", PAGE)
                elif self.path.startswith("/snapshot"):
                    jpeg = server.wait_frame(2.0)
                    self._send(200, "image/jpeg", jpeg) if jpeg else self._send(503, "text/plain", b"no frame yet")
                elif self.path.startswith("/stream"):
                    self.send_response(200)
                    self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    try:
                        while True:
                            jpeg = server.wait_frame(5.0)
                            if jpeg is None:
                                continue
                            self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                             + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n")
                    except (BrokenPipeError, ConnectionResetError):
                        return
                else:
                    self._send(404, "text/plain", b"not found")

            def _send(self, code, ctype, body):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = ThreadingHTTPServer((host, port), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True, name="mjpeg").start()

    def publish(self, image) -> None:
        ok, buf = self._cv2.imencode(".jpg", image, [self._cv2.IMWRITE_JPEG_QUALITY, self.quality])
        if ok:
            with self._cond:
                self._jpeg = buf.tobytes()
                self._cond.notify_all()

    def wait_frame(self, timeout: float) -> bytes | None:
        with self._cond:
            self._cond.wait(timeout)
            return self._jpeg

    def close(self) -> None:
        self.httpd.shutdown()
