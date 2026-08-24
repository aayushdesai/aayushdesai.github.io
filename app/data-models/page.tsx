import type { Metadata } from 'next';
import Link from 'next/link';
import { getPublishedReleases } from '@/content/releases';
import { ScrollProgress } from '../research-map';
import { SiteHeader } from '../site-header';

export const metadata: Metadata = {
  title: 'Data & models — Aayush Desai',
  description:
    'Public data, model, and software releases from Aayush Desai. Work in progress.',
  alternates: { canonical: '/data-models/' },
  openGraph: {
    title: 'Data & models — Aayush Desai',
    description:
      'Public data, model, and software releases from Aayush Desai. Work in progress.',
    url: '/data-models/',
  },
  twitter: {
    card: 'summary_large_image',
    title: 'Data & models — Aayush Desai',
    description:
      'Public data, model, and software releases from Aayush Desai. Work in progress.',
  },
};

export default function DataModelsPage() {
  const publishedReleases = getPublishedReleases();

  return (
    <main className="archive-page" id="top">
      <ScrollProgress />
      <SiteHeader current="data-models" />

      <section className="archive-hero" aria-labelledby="archive-title">
        <div className="archive-field" aria-hidden="true">
          <i />
          <i />
        </div>
        <div className="archive-hero-copy">
          <p className="eyebrow"><span /> Public research archive</p>
          <h1 id="archive-title">Data &amp; models.</h1>
          <p className="archive-status">Work in progress.</p>
          <p className="archive-intro">
            This will become the public record of the datasets, models, and
            software that sit behind my research. Files will live in durable
            repositories; their context will live here.
          </p>
        </div>
      </section>

      <section className="archive-body section-shell" aria-labelledby="release-index-title">
        <div className="section-label">Release archive</div>
        <div className="archive-statement">
          <h2 id="release-index-title">One page for each release.</h2>
          <p>
            Each public record will preserve the version, citation, files,
            provenance, reproduction notes, and known limitations in one place.
          </p>
        </div>

        <div className="archive-columns" aria-label="Planned release types">
          <article>
            <span>01</span>
            <p>Data</p>
            <h3>Measurements and derived products.</h3>
          </article>
          <article>
            <span>02</span>
            <p>Models</p>
            <h3>Grids, configurations, and physical outputs.</h3>
          </article>
          <article>
            <span>03</span>
            <p>Record</p>
            <h3>DOIs, checksums, provenance, and citation.</h3>
          </article>
        </div>

        <div className="release-index">
          <div className="release-index-heading">
            <p>Public releases</p>
            <span>{publishedReleases.length.toString().padStart(2, '0')}</span>
          </div>

          {publishedReleases.length > 0 ? (
            <div className="release-list">
              {publishedReleases.map((release) => (
                <Link className="release-row" href={`/data-models/${release.slug}/`} key={release.slug}>
                  <span>{release.kind}</span>
                  <div>
                    <h3>{release.title}</h3>
                    <p>{release.summary}</p>
                  </div>
                  <code>{release.version}</code>
                  <i aria-hidden="true">→</i>
                </Link>
              ))}
            </div>
          ) : (
            <div className="release-empty">
              <p>Nothing public here yet.</p>
              <span>The first release will appear when its record is complete.</span>
            </div>
          )}
        </div>
      </section>

      <footer className="archive-footer">
        <Link className="wordmark" href="/">AD<span className="wordmark-dot">.</span></Link>
        <p>A public record, built release by release.</p>
        <Link href="/">Return home →</Link>
      </footer>
    </main>
  );
}
