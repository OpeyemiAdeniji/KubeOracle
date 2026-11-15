# KubeOracle website

Static site, no build step: `index.html`, `styles.css`, `main.js`, `favicon.svg`.

Preview locally:

```bash
python -m http.server 4173 --directory website
```

Deploys to GitHub Pages from `main` through `.github/workflows/pages.yml` whenever files in `website/` change.
To use a custom domain, add a `CNAME` file containing the domain and point a DNS CNAME record at
`opeyemiadeniji.github.io`.
