import type { Metadata } from 'next';
import Link from 'next/link';
import { notFound } from 'next/navigation';
import { getPublishedReleases, getRelease } from '@/content/releases';
import { ScrollProgress } from '../../research-map';
import { SiteHeader } from '../../site-header';

type ReleasePageProps = {
  params: Promise<{ slug: string }>;
};

export const dynamicParams = false;

export function generateStaticParams() {
  const releaseParams = getPublishedReleases().map((release) => ({ slug: release.slug }));

  // Static export requires one parameter before the first public release exists.
  // This reserved path renders the normal 404 and disappears once the registry opens.
  return releaseParams.length > 0 ? releaseParams : [{ slug: '_reserved' }];
}

export async function generateMetadata({ params }: ReleasePageProps): Promise<Metadata> {
  const { slug } = await params;
  const release = getRelease(slug);

  if (!release) return {};

  const title = `${release.title} — Aayush Desai`;
  const pageUrl = `/data-models/${release.slug}/`;
  const images = release.socialImage
    ? [{ url: release.socialImage, alt: release.title }]
    : [];

  return {
    title,
    description: release.summary,
    alternates: { canonical: pageUrl },
    openGraph: {
      type: 'article',
      title,
      description: release.summary,
      url: pageUrl,
      images,
    },
    twitter: {
      card: release.socialImage ? 'summary_large_image' : 'summary',
      title,
      description: release.summary,
      images,
    },
  };
}

export default async function ReleasePage({ params }: ReleasePageProps) {
  const { slug } = await params;
  const release = getRelease(slug);

  if (!release) notFound();

  return (
    <main className="release-page" id="top">
      <ScrollProgress />
      <SiteHeader current="data-models" />

      <section className="release-hero" aria-labelledby="release-title">
        <div className="release-breadcrumb">
          <Link href="/data-models/">Data &amp; models</Link>
          <span>/</span>
          <span>{release.kind}</span>
        </div>
        <p className="eyebrow"><span /> {release.kind} release · {release.version}</p>
        <h1 id="release-title">{release.title}</h1>
        <p className="release-summary">{release.summary}</p>
        <ul className="release-topics" aria-label="Release topics">
          {release.topics.map((topic) => <li key={topic}>{topic}</li>)}
        </ul>
      </section>

      <section className="release-content section-shell">
        <aside className="release-metadata" aria-label="Release metadata">
          <dl>
            <div><dt>Status</dt><dd>{release.status}</dd></div>
            <div><dt>Version</dt><dd>{release.version}</dd></div>
            <div><dt>Date</dt><dd>{release.date}</dd></div>
            <div><dt>License</dt><dd>{release.license}</dd></div>
            {release.versionDoi && <div><dt>Version DOI</dt><dd>{release.versionDoi}</dd></div>}
            {release.conceptDoi && <div><dt>All versions</dt><dd>{release.conceptDoi}</dd></div>}
          </dl>
          <div className="release-links">
            {release.repository && <a href={release.repository.url}>{release.repository.label} ↗</a>}
            {release.relatedPublication && <a href={release.relatedPublication.url}>{release.relatedPublication.label} ↗</a>}
            {release.links?.map((link) => <a href={link.url} key={link.url}>{link.label} ↗</a>)}
          </div>
        </aside>

        <article className="release-article">
          <section>
            <h2>Overview</h2>
            {release.description.map((paragraph) => <p key={paragraph}>{paragraph}</p>)}
          </section>

          <section>
            <h2>Files</h2>
            <div className="release-files">
              {release.files.map((file) => (
                <article key={file.name}>
                  <div>
                    <h3>{file.name}</h3>
                    <p>{file.description}</p>
                  </div>
                  <dl>
                    <div><dt>Format</dt><dd>{file.format}</dd></div>
                    {file.size && <div><dt>Size</dt><dd>{file.size}</dd></div>}
                    {file.checksum && <div><dt>Checksum</dt><dd>{file.checksum}</dd></div>}
                  </dl>
                  {file.url && <a href={file.url}>Download ↗</a>}
                </article>
              ))}
            </div>
          </section>

          <ReleaseNotes title="Provenance" items={release.provenance} />
          <ReleaseNotes title="Reproduce" items={release.reproduce} ordered />
          <ReleaseNotes title="Known limitations" items={release.limitations} />

          {release.citation && (
            <section>
              <h2>Cite this release</h2>
              <pre className="release-citation">{release.citation}</pre>
            </section>
          )}
        </article>
      </section>

      <footer className="archive-footer">
        <Link className="wordmark" href="/">AD<span className="wordmark-dot">.</span></Link>
        <p>Versioned, citable, and explicit about its limits.</p>
        <Link href="/data-models/">All releases →</Link>
      </footer>
    </main>
  );
}

function ReleaseNotes({
  title,
  items,
  ordered = false,
}: {
  title: string;
  items: string[];
  ordered?: boolean;
}) {
  const List = ordered ? 'ol' : 'ul';

  return (
    <section>
      <h2>{title}</h2>
      <List className="release-notes">
        {items.map((item) => <li key={item}>{item}</li>)}
      </List>
    </section>
  );
}
