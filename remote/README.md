# PieCAD Remote (mobile viewer)

A CAD-agnostic web app: it only ever renders a GLB and reports
`{cad_object_name, world_position, text}`. It contains no FreeCAD,
Onshape, or SolidWorks-specific code -- see SceneModel.jsx.

## Dev
    npm install
    npm run dev
Open the printed LAN URL on your phone (same Wi-Fi as your laptop).
The backend must be running on :8000 (see core/api.py).

## Build (for the backend to serve at /remote)
    npm run build
This produces `remote/dist/`, which `core/api.py` mounts at `/remote`
automatically if the folder exists.
