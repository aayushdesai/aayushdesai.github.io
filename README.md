# Aayush Desai

Personal research website for Aayush Desai, astrophysicist at the Institute of
Science and Technology Austria.

The site is built with Next.js as a fully static export and published from the
`docs/` directory on GitHub Pages. Research files such as the CV and publication
record live in `public/`; the previous Jekyll source is retained in
`archive/legacy-jekyll/` for reference.

## Local development

```bash
npm install
npm run dev
```

## Production build

```bash
npm run build
```

The static website is written to `out/`. Copy the verified contents of `out/`
to `docs/` before committing a new public release. GitHub Pages serves `docs/`
from the `main` branch.
