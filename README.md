# MyVault

Private cloud for **files**, **links**, and **notes** — everything is stored
inside **your own MEGA account** (via the vendored `mega_local` package).
No external database, no GitHub configuration, nothing sensitive in git.

## Features

- **Files** — upload via file picker or live camera, saved to the correct
  MEGA sub-folder (`pdf/`, `excel/`, `json/`, `png/`, `jpg/`, `other/`)
- **Links** — save a URL with a title, description and *what it does*
- **Notes** — rich text with a **RGB colour** for visual grouping
- **Dashboard** — tabs for Files / Links / Notes, search box, floating
  **+** button, responsive card grid
- **Viewer** — inline image preview, PDF iframe, pretty-printed JSON,
  download button for everything else
- **Installable PWA** — Chrome (and most browsers) offer *Install app*
  thanks to `manifest.json` + `sw.js`

## Project layout
