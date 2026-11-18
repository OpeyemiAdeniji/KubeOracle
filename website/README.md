# KubeSift website

Two parts, deployed together to GitHub Pages by `.github/workflows/pages.yml` when `website/`, `docs/` or
`mkdocs.yml` change on `main`:

| Path | Source | Build |
|---|---|---|
| `/KubeSift/` | this folder: `index.html`, `styles.css`, `main.js`, `favicon.svg`, `social-preview.png` | none, static |
| `/KubeSift/docs/` | `../docs/*.md` | MkDocs Material, configured in `../mkdocs.yml` |

Preview both locally, as Pages serves them:

```bash
pip install "mkdocs==1.6.1" "mkdocs-material==9.8.1"
mkdocs build --site-dir _site/docs && cp -r website/. _site/
python -m http.server 4173 --directory _site
```

`favicon.svg` is the logo (also the Helm chart icon). `social-preview.png` (1280×640) is the link preview image;
also upload it in the repository's Settings → General → Social preview.

To use a custom domain, add a `CNAME` file containing the domain and point a DNS CNAME record at
`opeyemiadeniji.github.io`.
