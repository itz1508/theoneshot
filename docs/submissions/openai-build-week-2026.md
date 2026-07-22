# OpenAI Build Week 2026 — Submission Record

This is a lightweight pointer to the preserved OpenAI Build Week 2026 submission
artifacts for **The One Shot**. The full artifacts (final video, per-product cuts,
narration, captions, storyboards, evidence claims, and assembly scripts) are kept off
`main` to keep the working tree lean; they live on a dedicated archive branch and are
not part of the product source.

## Where the artifacts live

| Item | Value |
|---|---|
| Archive branch | `submission-archive` |
| Archive commit | `c208a679891df52715850f167e0aa1747ae9e514` |
| Archived path | `TheOneShot_Submission/` |
| Removed from `main` | Yes (artifacts were never tracked on `main`; no history rewrite was performed) |

To inspect the archived artifacts:

```powershell
git ls-tree -r --name-only submission-archive -- TheOneShot_Submission
git show submission-archive:TheOneShot_Submission/MASTER_EDIT_REPORT.md
```

## Final deliverable

| Item | Value |
|---|---|
| Final MP4 (authoritative) | `C:\Users\itz15\Videos\TheOneShot_Final.mp4` |
| Archived working copy | `TheOneShot_Submission/_final/TheOneShot_Final.mp4` |
| Duration | 154.57 s (2:34.6), under the 2:50 limit |
| Resolution | 1920×1080, H.264 video / AAC audio |
| Captions | Burned-in SRT |
| Narration | English Windows SAPI TTS |

## GHCR submission images (anonymously pullable)

Verified after `docker logout ghcr.io`:

| Image | Digest |
|---|---|
| `ghcr.io/itz1508/theoneshot-aflow:submission-20260721` | `sha256:a98016deffb5b7bf80e5d9f27c75bcdd7552cafa1e040155e305ff6519ce880e` |
| `ghcr.io/itz1508/theoneshot-fix:submission-20260721` | `sha256:70efacda424c7b7937f10a6d42fcc66e3160e8a86800570a2fb9d60f8828a8d6` |
| `ghcr.io/itz1508/theoneshot-audisor-agent:submission-20260721` | `sha256:762763aeb3683575582dfa74997a5d45294bd3f62159909453b31451e15f6b20` |

```powershell
docker pull ghcr.io/itz1508/theoneshot-aflow:submission-20260721
docker pull ghcr.io/itz1508/theoneshot-fix:submission-20260721
docker pull ghcr.io/itz1508/theoneshot-audisor-agent:submission-20260721
```

## Session / footage provenance

The final render is assembled from 23 unique usable clips drawn from 45 recorded
`.mp4` screen-capture files (duplicate `Recording ...` / `Screen Recording ...` pairs
retained in the archive). The plan-validation session footage includes
`Screen Recording 2026-07-19 172830.mp4` and `Recording 2026-07-19 172908.mp4`.
Full provenance is recorded in
`TheOneShot_Submission/FOOTAGE_INDEX.md` and
`TheOneShot_Submission/MASTER_EDIT_REPORT.md` on the `submission-archive` branch.

## Note

These submission images expose the historical submission CLI surface, which differs
from the current local source CLI. They are preserved as-is for provenance and are not
rebuilt. Production images are built from the pinned Dockerfiles under `packaging/`
and `audisor/docker/` and are versioned separately (see the root `README.md`).
