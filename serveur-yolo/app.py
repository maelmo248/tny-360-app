"""
Serveur de suivi de personne (YOLO11s + Flask)
================================================

- Lit un flux MJPEG HTTP (ex: caméra IP) à l'adresse MJPEG_URL
- Détecte UNIQUEMENT la classe "person" avec YOLO11s (ultralytics)
- Dessine un rectangle BLEU autour de chaque personne détectée
- Sert :
    * une page web  ->  http://<ip_serveur>:5000/
        - flux vidéo annoté
        - coordonnées affichées sous la vidéo
    * un flux MJPEG ->  http://<ip_serveur>:5000/video_feed
    * une API JSON  ->  http://<ip_serveur>:5000/coords
                        http://<ip_serveur>:5000/coords/primary
      (pensées pour être appelées facilement depuis un script,
       typiquement un programme TypeScript avec fetch())

Les poids "yolo11s.pt" sont téléchargés automatiquement par ultralytics
au premier lancement s'ils ne sont pas déjà présents localement.
"""

import threading
import time
import traceback
from datetime import datetime, timezone

import cv2
import numpy as np
import requests
from flask import Flask, Response, jsonify, render_template_string
from flask_cors import CORS
from ultralytics import YOLO

# Évite les conflits de threads entre OpenCV et PyTorch, cause connue de
# blocages silencieux (le thread d'inférence se fige sans lever d'exception).
cv2.setNumThreads(1)
try:
    import torch
    torch.set_num_threads(1)
except Exception:
    pass

# ============================== CONFIGURATION ==============================

# Adresse du flux MJPEG. Adapte le chemin si ta caméra en a besoin
# (par exemple "http://192.168.1.130:90/video" ou "/mjpeg", "/stream", etc.)
MJPEG_URL = "http://192.168.1.30:90"

MODEL_NAME = "yolo26n.pt"        # téléchargé automatiquement si absent
CONF_THRESHOLD = 0.1          # seuil de confiance minimum
PERSON_CLASS_ID = 0              # classe "person" dans le dataset COCO

JPEG_QUALITY = 80                # qualité de ré-encodage du flux annoté
BOX_COLOR_BGR = (255, 0, 0)      # BLEU en OpenCV (format B, G, R)
BOX_THICKNESS = 2
FONT = cv2.FONT_HERSHEY_SIMPLEX

SERVER_HOST = "0.0.0.0"
SERVER_PORT = 5000

STREAM_FPS_LIMIT = 25            # limite d'envoi du flux annoté au navigateur
RECONNECT_DELAY_SEC = 2          # délai avant de retenter la connexion caméra

# =============================================================================

app = Flask(__name__)
CORS(app)  # autorise les appels cross-origin (utile si un jour appelé depuis un navigateur)

print(f"[INFO] Chargement du modèle {MODEL_NAME} (téléchargement auto si besoin)...")
model = YOLO(MODEL_NAME)
print("[INFO] Modèle chargé.")

model_lock = threading.Lock()

# ------------------------- État partagé (thread-safe) ----------------------
state_lock = threading.Lock()
latest_jpeg = None  # dernière frame annotée, encodée en JPEG (bytes)
latest_detections = {
    "timestamp": None,       # ISO 8601 UTC
    "frame_width": None,
    "frame_height": None,
    "count": 0,
    "persons": [],            # liste de {id, bbox:[x1,y1,x2,y2], center:[cx,cy], confidence}
    "primary": None,          # la personne "principale" (plus grande boîte) ou None
}
stream_status = {"connected": False, "last_error": None}
last_frame_time = None  # horodatage (time.time()) de la dernière frame traitée avec succès


# ================================ MJPEG READER ==============================

class MJPEGReader:
    """Lit un flux MJPEG HTTP brut (multipart) et renvoie des frames OpenCV (BGR)."""

    def __init__(self, url, timeout=10):
        self.url = url
        self.timeout = timeout
        self._resp = None
        self._iter = None
        self._buffer = b""

    def connect(self):
        self.close()
        self._resp = requests.get(
            self.url, stream=True, timeout=(5, self.timeout),
            headers={"Cache-Control": "no-cache"},
        )
        self._resp.raise_for_status()
        self._iter = self._resp.iter_content(chunk_size=16384)
        self._buffer = b""

    def read_frame(self):
        """Retourne une frame décodée (np.ndarray) dès qu'une image JPEG complète est reçue."""
        for chunk in self._iter:
            if not chunk:
                continue
            self._buffer += chunk
            start = self._buffer.find(b"\xff\xd8")
            if start > 0:
                self._buffer = self._buffer[start:]
                start = 0
            end = self._buffer.find(b"\xff\xd9", start + 2)
            if start == 0 and end != -1:
                jpg = self._buffer[:end + 2]
                self._buffer = self._buffer[end + 2:]
                frame = cv2.imdecode(np.frombuffer(jpg, dtype=np.uint8), cv2.IMREAD_COLOR)
                if frame is not None:
                    return frame
            if len(self._buffer) > 8 * 1024 * 1024:
                self._buffer = b""
        return None  # flux terminé

    def close(self):
        if self._resp is not None:
            try:
                self._resp.close()
            except Exception:
                pass
        self._resp = None
        self._iter = None
        self._buffer = b""


# ================================ TRAITEMENT ================================

def process_frame(frame):
    """Fait tourner YOLO11s sur une frame, ne garde que les personnes, annote en bleu."""
    with model_lock:
        results = model.predict(
            frame,
            classes=[PERSON_CLASS_ID],
            conf=CONF_THRESHOLD,
            verbose=False,
        )

    persons = []
    h, w = frame.shape[:2]

    if len(results) > 0:
        boxes = results[0].boxes
        for i, box in enumerate(boxes):
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            conf = float(box.conf[0])
            cx = int((x1 + x2) / 2)
            cy = int((y1 + y2) / 2)

            persons.append({
                "id": i,
                "bbox": [int(x1), int(y1), int(x2), int(y2)],
                "center": [cx, cy],
                "confidence": round(conf, 3),
                "area": int((x2 - x1) * (y2 - y1)),
            })

            # Dessin : rectangle bleu + point central + texte coordonnées
            cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), BOX_COLOR_BGR, BOX_THICKNESS)
            cv2.circle(frame, (cx, cy), 4, BOX_COLOR_BGR, -1)
            label = f"person {conf:.2f} ({cx},{cy})"
            cv2.putText(frame, label, (int(x1), max(int(y1) - 8, 12)), FONT, 0.5, BOX_COLOR_BGR, 2)

    # La personne "principale" = la plus grande boîte (probablement la plus proche)
    primary = None
    if persons:
        primary_full = max(persons, key=lambda p: p["area"])
        primary = {
            "id": primary_full["id"],
            "bbox": primary_full["bbox"],
            "center": primary_full["center"],
            "confidence": primary_full["confidence"],
        }
    persons_public = [{k: v for k, v in p.items() if k != "area"} for p in persons]

    detections = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "frame_width": w,
        "frame_height": h,
        "count": len(persons_public),
        "persons": persons_public,
        "primary": primary,
    }
    return frame, detections


def capture_loop():
    """Boucle infinie : connexion caméra -> lecture -> inférence -> mise à jour état global."""
    global latest_jpeg, latest_detections, last_frame_time

    reader = MJPEGReader(MJPEG_URL)
    frame_counter = 0
    last_heartbeat = 0.0

    while True:
        try:
            print(f"[INFO] Connexion au flux MJPEG : {MJPEG_URL}")
            reader.connect()
            with state_lock:
                stream_status["connected"] = True
                stream_status["last_error"] = None

            while True:
                frame = reader.read_frame()
                if frame is None:
                    raise ConnectionError("Flux MJPEG interrompu (plus de données).")

                # Le traitement (inférence YOLO + dessin) est isolé : si une frame
                # particulière pose problème, on la saute au lieu de geler tout le flux.
                try:
                    annotated, detections = process_frame(frame)
                except Exception:
                    print("[ERREUR] Échec du traitement d'une frame (frame ignorée) :")
                    traceback.print_exc()
                    continue

                ok, buf = cv2.imencode(
                    ".jpg", annotated, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY]
                )
                if not ok:
                    continue

                with state_lock:
                    latest_jpeg = buf.tobytes()
                    latest_detections = detections
                    last_frame_time = time.time()

                frame_counter += 1
                now = time.time()
                if now - last_heartbeat > 5:
                    print(f"[INFO] Flux actif — frame #{frame_counter}, "
                          f"{detections['count']} personne(s) détectée(s)")
                    last_heartbeat = now

        except Exception as exc:  # connexion perdue, caméra inaccessible, etc.
            print(f"[ERREUR] {exc}")
            traceback.print_exc()
            with state_lock:
                stream_status["connected"] = False
                stream_status["last_error"] = str(exc)
            reader.close()
            time.sleep(RECONNECT_DELAY_SEC)


# ================================== ROUTES ==================================

INDEX_HTML = """
<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>Suivi de personne - YOLO11s</title>
<style>
  body { background:#111; color:#eee; font-family:system-ui,sans-serif; margin:0; padding:24px; }
  main { width:min(100%, 1000px); margin:0 auto; display:flex; flex-direction:column; align-items:center; }
  h1 { font-weight:600; margin:0 0 4px; text-align:center; }
  #status { margin:0 0 16px; font-size:14px; text-align:center; }
  #status.ok { color:#4caf50; }
  #status.ko { color:#f44336; }
  img#video { display:block; width:min(100%, 900px); height:auto; border:2px solid #333; border-radius:8px; }
  #panel { box-sizing:border-box; width:min(100%, 600px); margin:20px auto 0; text-align:left; background:#1c1c1c;
           padding:16px 24px; border-radius:8px; }
  #panel h2 { font-size:16px; margin-top:0; }
  table { border-collapse:collapse; width:100%; }
  td, th { padding:4px 8px; font-size:13px; border-bottom:1px solid #333; }
  th { text-align:left; color:#999; }
  .dot { display:inline-block; width:10px; height:10px; border-radius:50%; background:#2196f3; margin-right:6px; }
</style>
</head>
<body>
  <main>
  <h1>Suivi de personne (YOLO11s)</h1>
  <div id="status">connexion...</div>
  <img id="video" src="/video_feed" alt="flux vidéo">

  <div id="panel">
    <h2><span class="dot"></span>Coordonnées détectées</h2>
    <table>
      <tr><th>Nb personnes</th><td id="count">-</td></tr>
      <tr><th>Centre (principale)</th><td id="primary-center">-</td></tr>
      <tr><th>Boîte (principale)</th><td id="primary-bbox">-</td></tr>
      <tr><th>Confiance</th><td id="primary-conf">-</td></tr>
      <tr><th>Dernière mise à jour</th><td id="ts">-</td></tr>
    </table>
  </div>
  </main>

<script>
async function refresh() {
  try {
    const res = await fetch('/coords', { cache: 'no-store' });
    const data = await res.json();
    document.getElementById('count').textContent = data.count;
    document.getElementById('ts').textContent = data.timestamp ?? '-';
    if (data.primary) {
      document.getElementById('primary-center').textContent =
        `(${data.primary.center[0]}, ${data.primary.center[1]})`;
      document.getElementById('primary-bbox').textContent =
        `[${data.primary.bbox.join(', ')}]`;
      document.getElementById('primary-conf').textContent = data.primary.confidence;
    } else {
      document.getElementById('primary-center').textContent = '-';
      document.getElementById('primary-bbox').textContent = '-';
      document.getElementById('primary-conf').textContent = '-';
    }

    const res2 = await fetch('/status', { cache: 'no-store' });
    const s = await res2.json();
    const el = document.getElementById('status');
    if (s.connected) {
      el.textContent = 'Caméra connectée';
      el.className = 'ok';
    } else {
      el.textContent = 'Caméra déconnectée' + (s.last_error ? ' — ' + s.last_error : '');
      el.className = 'ko';
    }
  } catch (e) {}
}
async function poll() {
  await refresh();
  setTimeout(poll, 500);
}
poll();
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(INDEX_HTML)


def mjpeg_generator():
    """N'envoie une image que lorsqu'une nouvelle frame a été traitée."""
    last_sent_frame_time = None
    min_interval = 1.0 / STREAM_FPS_LIMIT

    while True:
        with state_lock:
            frame = latest_jpeg
            frame_time = last_frame_time

        if frame is not None and frame_time != last_sent_frame_time:
            now = time.time()
            if last_sent_frame_time is None or now - last_sent_frame_time >= min_interval:
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Cache-Control: no-cache, no-store, must-revalidate\r\n\r\n"
                    + frame + b"\r\n"
                )
                last_sent_frame_time = frame_time
                continue

        time.sleep(0.01)


@app.route("/video_feed")
def video_feed():
    return Response(mjpeg_generator(), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/coords")
def coords():
    """Toutes les détections courantes (JSON). Endpoint principal pour un script externe."""
    with state_lock:
        return jsonify(latest_detections)


@app.route("/coords/primary")
def coords_primary():
    """Raccourci : uniquement la personne principale (ou null). Pratique pour le robot."""
    with state_lock:
        primary = latest_detections["primary"]
        ts = latest_detections["timestamp"]
    return jsonify({"timestamp": ts, "primary": primary})


@app.route("/status")
def status():
    with state_lock:
        return jsonify(stream_status)


# =================================== MAIN ====================================

if __name__ == "__main__":
    t = threading.Thread(target=capture_loop, daemon=True)
    t.start()
    print(f"[INFO] Serveur web démarré sur http://{SERVER_HOST}:{SERVER_PORT}")
    app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True)