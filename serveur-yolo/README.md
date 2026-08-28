# Serveur de suivi de personne (YOLO11s + Flask)

## Installation

```bash
pip install -r requirements.txt
```

`ultralytics` téléchargera automatiquement les poids `yolo11s.pt` (~20 Mo) au tout premier lancement s'ils ne sont pas déjà présents dans le dossier courant.

## Configuration

Ouvre `app.py` et vérifie en haut du fichier :

```python
MJPEG_URL = "http://192.168.1.130:90"
```

Si ta caméra expose le flux sur un chemin particulier (ex: `/video`, `/mjpeg`, `/stream.mjpg`...), ajoute-le à l'URL.

## Lancement

```bash
python app.py
```

Puis ouvre dans un navigateur : **http://localhost:5000**

Tu verras :
- le flux vidéo avec un **rectangle bleu** autour de chaque personne détectée (+ un point au centre)
- un panneau sous la vidéo avec les coordonnées de la personne "principale" (celle dont la boîte est la plus grande, donc probablement la plus proche)

## API JSON (pour ton script TypeScript)

Deux endpoints pensés pour être appelés facilement depuis n'importe quel programme :

### `GET /coords` — toutes les détections

```json
{
  "timestamp": "2026-08-27T10:15:32.123456+00:00",
  "frame_width": 640,
  "frame_height": 480,
  "count": 1,
  "persons": [
    {"id": 0, "bbox": [120, 80, 340, 460], "center": [230, 270], "confidence": 0.91}
  ],
  "primary": {"id": 0, "bbox": [120, 80, 340, 460], "center": [230, 270], "confidence": 0.91}
}
```

### `GET /coords/primary` — raccourci pratique pour le suivi robot

```json
{
  "timestamp": "2026-08-27T10:15:32.123456+00:00",
  "primary": {"id": 0, "bbox": [120, 80, 340, 460], "center": [230, 270], "confidence": 0.91}
}
```

`primary` vaut `null` si aucune personne n'est détectée.

### `GET /status` — état de la connexion caméra

```json
{"connected": true, "last_error": null}
```

## Exemple d'appel depuis TypeScript (aperçu)

Juste pour te donner une idée de l'intégration côté robot plus tard (Node.js, `fetch` natif à partir de Node 18+) :

```typescript
async function getPersonCenter(): Promise<[number, number] | null> {
  const res = await fetch("http://<ip_du_serveur_python>:5000/coords/primary");
  const data = await res.json();
  if (!data.primary) return null;
  return data.primary.center as [number, number]; // [cx, cy]
}

// Exemple de boucle de suivi (pseudo-code)
setInterval(async () => {
  const center = await getPersonCenter();
  if (center) {
    // -> ici tu appelleras le SDK du robot pour orienter/avancer
    console.log("Personne détectée au centre :", center);
  }
}, 300);
```

Comme précisé, ce n'est qu'un exemple d'appel — la logique de pilotage du robot (SDK TypeScript) sera à construire dans une étape suivante.

## Notes / réglages possibles

- `CONF_THRESHOLD` (dans `app.py`) : seuil de confiance minimum, à ajuster selon les faux positifs/négatifs.
- `STREAM_FPS_LIMIT` : limite le débit du flux annoté envoyé au navigateur (n'affecte pas la fréquence d'inférence).
- Le serveur se reconnecte automatiquement si le flux MJPEG est interrompu.
- Si le CPU est trop sollicité par YOLO11s, tu peux modifier `process_frame` pour ne faire l'inférence qu'une frame sur N (les autres frames sont alors juste réaffichées sans ré-analyse).
