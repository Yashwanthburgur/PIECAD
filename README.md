# PieCAD — Full Run Guide (Desktop + Mobile Remote)

This is the complete, current path to running PieCAD end to end: FreeCAD →
backend → desktop panel → mobile viewer. It supersedes any earlier partial
instructions — everything below reflects the fixes made after the initial
integration (missing `importGLTF`, scale mismatch, slow mobile rendering,
face highlighting).

## 0. What you need installed

- FreeCAD (with its own bundled Python — separate from your project's `uv` environment)
- Node.js + npm (for the mobile viewer)
- Your existing `uv`-managed Python environment for the backend

## 1. One-time setup

### 1a. Install `trimesh` into FreeCAD's OWN Python

FreeCAD ships its own embedded Python interpreter. This is **not** the
same Python your backend runs in — installing `trimesh` via your normal
`uv`/`pip` won't make it available inside FreeCAD.

In FreeCAD's Python console:

```python
import subprocess, sys
subprocess.check_call([sys.executable, "-m", "pip", "install", "trimesh"])
```

Verify it worked: `import trimesh` should run with no error, in that same console.

> Why trimesh at all: FreeCAD's bundled `importGLTF` module isn't present
> in every FreeCAD build/version (confirmed missing in this project's
> setup via `ModuleNotFoundError`). Rather than chase FreeCAD packaging
> differences, the exporter tessellates shapes directly with
> `Shape.tessellate()` and writes GLB with `trimesh`, which has no such
> dependency.

### 1b. Sync backend dependencies

From the `PIECAD/` repo root:

```bash
uv sync
```

This should pick up `qrcode[pil]` (QR pairing) and `uvicorn[standard]`
(WebSocket support) from `pyproject.toml`.

### 1c. Install the mobile viewer's dependencies

```bash
cd PIECAD/remote
npm install
```

### 1d. Build the mobile viewer once

```bash
npm run build
```

This produces `remote/dist/`, which the backend serves directly at
`/remote` — one process, one origin, no dev-server juggling, and the QR
pairing URL resolves correctly (it points at port 8000, not Vite's 5173).
Re-run this build any time you change files under `remote/src/`.

## 2. Every time you want to run PieCAD

### Step 1 — Start FreeCAD and the XML-RPC bridge

Open FreeCAD, open its Python console, and run your existing bridge
startup sequence:

```python
import sys, threading
from pathlib import Path
sys.path.insert(0, str(PROJECT_ROOT / "adapters/freecad"))  # adjust to your actual path
import bridge

bridge.install_main_thread_processor()

t = threading.Thread(target=lambda: bridge.start(port=9876), daemon=True)
t.start()

panel_path = PROJECT_ROOT / "ui/freecad_panel.py"
with open(panel_path, encoding="utf-8") as f:
    exec(f.read())
```

**If you ever edit `bridge.py`, you must close and reopen FreeCAD, then
re-run this whole sequence.** Python caches the imported module in
memory — saving the file on disk does nothing to an already-running
FreeCAD session. This was the cause of one "fix didn't work" earlier in
this project's setup.

### Step 2 — Start the backend

From `PIECAD/` root:

```bash
uv run uvicorn core.api:app --host 0.0.0.0 --port 8000 --reload
```

`--host 0.0.0.0` is not optional — a phone on your Wi-Fi cannot reach a
server bound to `127.0.0.1`.

### Step 3 — Sanity-check the export before touching the phone

Open this directly in a browser on the same computer:

```
http://127.0.0.1:8000/api/state/glb
```

- A `.glb` file downloads → export is working, move on.
- An error page with real text → read it; that tells you exactly what's
  wrong (missing module, no visible objects, etc.) rather than guessing
  from a blank screen on the phone.

### Step 4 — Pair your phone

1. In the FreeCAD dock panel, click **Pair Phone**.
2. A QR code and URL appear in a dialog.
3. On your phone (same Wi-Fi network as your computer — not cellular
   data, and not a guest network that isolates client devices from each
   other), scan the QR code and open the link.

### Step 5 — Use it

- Create geometry from the desktop panel as usual, or type/speak an
  instruction on the phone after tapping a face.
- The phone auto-refreshes shortly after any change (WebSocket
  "scene_updated" ping -> refetch GLB).
- Tapping a face now visibly highlights it (orange) before you send an
  instruction -- see "Face highlighting" below for how this works.

## 3. Performance notes (why it used to feel slow, and what changed)

Two separate bottlenecks were addressed:

1. **Triangle count.** The original exporter tessellated every curved
   face at a fixed, very fine 0.1mm deflection regardless of part size.
   Flat faces (box sides) are unaffected by deflection -- they're always
   2 triangles -- but cylinders and fillets were generating far more
   detail than a phone screen can even show, bloating both export time
   and transfer size. Deflection is now adaptive: it scales with each
   object's bounding-box diagonal (clamped between 0.05mm and 2mm), so a
   100mm part gets proportionally coarser (and faster) curved-surface
   tessellation with no visible quality loss.
2. **Transfer size over Wi-Fi.** The backend now gzip-compresses
   responses over 500 bytes (`GZipMiddleware`), which helps the GLB
   payload move faster over a typical home Wi-Fi connection.

If it's still slow with a genuinely large/complex model, the next lever
to pull is lowering the deflection formula's multiplier further in
`bridge.py`'s `_impl_export_glb`, or reducing how much geometry is
visible at once (hide finished sub-assemblies).

## 4. Face highlighting -- how it works

Previously, each CAD object exported as a single fused mesh with no
memory of which triangles belonged to which B-rep face, so there was
nothing to recolor on tap. The exporter now:

1. Tessellates each face of an object **separately** (not merged),
2. Records each face's triangle range (`tri_start`, `tri_count`) and its
   stable id (`"ObjectName_face_N"`, matching what `get_faces` already
   returns) in the mesh's `metadata`,
3. trimesh writes that metadata into the exported glTF node's `extras`
   field.

three.js's `GLTFLoader` automatically exposes glTF `extras` as
`object.userData` on load -- **no new endpoint or custom parsing needed**.
The viewer (`SceneModel.jsx`) reads `mesh.userData.piecad_faces`, builds
one three.js geometry "group" and material per face, and on tap uses the
raycast hit's `materialIndex` to know exactly which face was touched --
recoloring just that face orange, and reverting the previous selection
back to its base color.

This is purely a client-side visual -- it doesn't change what gets sent
to the backend (`cad_object_name` + `world_position` + text), which still
independently resolves the nearest face server-side via the existing
`get_faces` tool. If a future export is missing this metadata for any
reason, the viewer falls back to one uniform material per object:
selection still works, it just can't highlight a single face.

## 5. Troubleshooting quick-reference

| Symptom | Cause | Fix |
|---|---|---|
| Phone shows a blank page, JS 404s in backend log | Built app's asset paths don't match the `/remote` mount | Ensure `remote/vite.config.js` has `base: "/remote/"`, then `npm run build` again |
| `{"error": "...No module named 'importGLTF'..."}` | Your FreeCAD build doesn't ship that module | Already fixed -- exporter now uses `trimesh`, not `importGLTF` |
| Backend log shows 200 OK everywhere but phone still black | Old code cached in a running FreeCAD session | Restart FreeCAD fully, re-run the bridge startup sequence |
| Model loads but is a tiny invisible speck | Export was scaled to meters while the viewer's camera assumes millimeter-scale geometry | Already fixed -- exporter and `coords.js` both stay in millimeters, no scale conversion |
| Phone can't reach the backend at all | Backend bound to `127.0.0.1` instead of the LAN interface | Always run with `--host 0.0.0.0` |
| Hole/edit lands on the wrong face | Coordinate convention mismatch between the exporter and the viewer | Verify empirically: tap dead-center of a known face, confirm the resolved `face_id` in the backend log matches expectation; adjust `coords.js` if not |
