---
name: "real-nifti-imaging"
description: "Integrates real NIfTI brain imaging (MRI/PET) into the AD-Screen Vue3+FastAPI viewer, replacing synthetic placeholders with actual axial slices. Invoke when user wants real medical imaging in the viewer, real image thumbnails, or uploading .nii to see actual brain scans."
---

# Real NIfTI Imaging Visualization

End-to-end recipe for wiring real NIfTI (`.nii`) brain volumes into the AD-Screen medical viewer so uploaded cases show actual brain anatomy instead of synthetic placeholder images. Covers backend slice rendering + frontend dual-mode viewport (real ↔ synthetic fallback).

## When to Invoke

- User uploads real `.nii` MRI/PET and wants to view real brain slices in the viewer
- User asks to replace synthetic ROI / placeholder images with real scans
- User wants real image thumbnails in analysis detail / report pages
- Any "show actual medical imaging" request for this project

## Project Layout

```
TransMF_AD-master/
├── ad-screen-backend/
│   ├── services/imaging_service.py   # NIfTI load + LRU + slice PNG
│   ├── routers/imaging.py            # /case/{id}/imaging/{meta,slice}
│   ├── main.py                       # register imaging.router
│   └── .venv/ (project root)         # nibabel, PIL, numpy, scipy; NO matplotlib
├── ad-screen-frontend/
│   ├── src/api/imaging.ts            # meta + sliceUrl + loadSliceGray + real thumbnail
│   ├── src/components/MedicalViewport.vue  # dual-mode: real props + synthetic fallback
│   ├── src/views/viewer/ViewerView.vue     # fetch meta, pass realCaseId/sliceCount
│   ├── src/views/analysis/AnalysisDetailView.vue
│   └── src/views/report/ReportView.vue
```

## Backend

### imaging_service.py
- `OUT_SIZE = 256` (must match frontend `SLICE_SIZE`)
- `VolumeData`: holds float32 volumes + per-modality percentile display window (1%–99% of nonzero voxels)
- `_load_canonical(path)` → `nibabel.load` + `nib.as_closest_canonical` (RAS orientation)
- `_axial_slice(vol3d, idx)` → `np.flipud(vol3d[:,:,idx].T)` (radiological axial convention: left=R, top=A)
- `_to_uint8(slice2d, lo, hi)` → clip + linear map 0–255
- `render_slice_png()` → `Image.fromarray(u8,'L').resize((256,256), BILINEAR)` → PNG bytes
- LRU cache (max 3 volumes) with thread lock; single-modality failure must not block the other
- `peek_slice_count(path)` → read header only for fast meta response

### routers/imaging.py
- `GET /case/{id}/imaging/meta` → `{available, mri, pet, sliceCount, sliceSize:256, orientation:"axial"}`
- `GET /case/{id}/imaging/slice?modality=MRI|PET&idx=n` → raw PNG with `Cache-Control: public, max-age=3600`
- **Failures return naked 404** (not JSON) — frontend catches per-slice and falls back

### main.py
```python
from routers import imaging
api_router.include_router(imaging.router)  # after case.router
```

## Frontend

### api/imaging.ts
- `apiGetImagingMeta(caseId)` → real call via `httpGet`; Mock returns `available:false`
- `apiSliceUrl(caseId, modality, idx)` → raw PNG URL (use `<img>`/`fetch`, NOT axios — interceptor rejects non-JSON 404)
- `loadSliceGray(caseId, modality, idx)` → `<img>` → canvas → `getImageData` → `Uint8Array` (take R channel, PNG is L mode)
- `apiGetRealThumbnail(caseId, modality, idx)` → canvas-composite thumbnail (MRI gray / PET Jet / fusion alpha) → dataURL

### MedicalViewport.vue (dual-mode)
New optional props:
```ts
realCaseId?: string      // case id; '' = synthetic mode
realAvailable?: boolean  // does this viewport's modality have real data?
```

Logic:
- `useReal = !!realCaseId && realAvailable && !realFailed`
- `getSlice(idx)` → real: async fetch+decode PNG, cache 40 layers, prefetch neighbors; any fail → `realFailed=true`, fall back to synthetic
- MRI viewport loads only MRI; PET only PET; FUSION loads both
- Real mode sets `wl = {ww:255, wc:128}` (full window, backend already windowed)
- **Disable synthetic ROI overlays in real mode** (`if (showRoi && !useReal)`) — synthetic ellipses don't match real anatomy
- Show "加载中…" overlay while current slice is decoding
- Synthetic path (`generateSliceData`) is unchanged; zero code duplication

### ViewerView.vue
- onMounted: `apiGetImagingMeta(caseId)` → `imagingMeta`
- `viewSliceCount = meta?.sliceCount ?? 256`
- If real: `sliceIdx = floor(sliceCount/2)` (128 layers → mid=64; synthetic is 256 → 128)
- Pass `:real-case-id`, `:real-available`, `:slice-count` to each viewport
- FUSION: `realAvailable = mri && pet`

### Thumbnails (AnalysisDetailView, ReportView)
- First render synthetic fallback immediately
- Then `apiGetImagingMeta` → if available, call `apiGetRealThumbnail` for MRI/PET/FUSION at mid slice
- Per-modality fallback: `real ?? synthetic`

## Contract

| Field | Value |
|-------|-------|
| Slice size | 256×256, 8-bit single-channel PNG |
| Axial layers | 128 (real) / 256 (synthetic) |
| Orientation | RAS, radiological convention (L=R, T=A) |
| Meta response | `{code:200, message, data:{...}}` |
| Slice response | raw `image/png` or 404 |
| Slice URL | `${API_BASE}/case/{id}/imaging/slice?modality=MRI|PET&idx={n}` |

## Gotchas (do NOT repeat)

1. **No matplotlib in venv** — use PIL/numpy only
2. **Backend runs without `--reload`** — must restart manually after code changes
3. **`<img>` cannot carry JWT** — slice endpoint must NOT require auth (project has no global auth middleware, so this is fine)
4. **axios interceptor rejects non-2xx** — slice 404 must be fetched via raw `<img>`/`fetch`, not `httpGet`
5. **`autoflush=False`** in DB — seed data must set `mri_path`/`pet_path` directly in the dict before commit
6. **Windows MONAI** — set `monai.transforms.transform.MAX_SEED = 2**31`
7. **ORM `models/` vs training `models/`** — `sys.path.append` (not insert); clear `sys.modules['models*']` before loading trained model
8. **Slice path depth** — 3 `dirname` calls from `services/` to project root

## Verification

```powershell
# Backend meta
curl http://localhost:8000/api/case/AD260001/imaging/meta
# → {"code":200,"data":{"available":true,"mri":true,"pet":true,"sliceCount":128,...}}

# Slice PNG
curl -o slice.png "http://localhost:8000/api/case/AD260001/imaging/slice?modality=MRI&idx=64"
# verify L mode 256×256

# Frontend strict type check
cd ad-screen-frontend && npm run build

# Browser E2E
# - AD260001 (real): 3 viewports show real brain, no ROI, layer 65/128
# - AD260006 (no real): synthetic fallback, ROI present, layer 129/256
# - Analysis detail / report thumbnails = real mid-slice
```
