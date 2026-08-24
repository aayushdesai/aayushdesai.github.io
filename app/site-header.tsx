import Link from 'next/link';

type SiteHeaderProps = {
  current?: 'data-models';
};

export function SiteHeader({ current }: SiteHeaderProps) {
  return (
    <header className="site-header">
      <Link className="wordmark" href="/" aria-label="Aayush Desai, home">
        AD<span className="wordmark-dot">.</span>
      </Link>
      <nav className="site-nav" aria-label="Primary navigation">
        <Link href="/#work">Work</Link>
        <Link href="/#publications">Publications</Link>
        <Link href="/data-models/" aria-current={current === 'data-models' ? 'page' : undefined}>
          Data &amp; models
        </Link>
        <Link href="/#about">About</Link>
        <Link href="/#notes">Notes</Link>
        <a href="/Aayush-Alpesh-Desai-CV.pdf" target="_blank" rel="noreferrer">CV</a>
      </nav>
      <a className="contact-link" href="mailto:adesai@ista.ac.at">
        Say hello <span aria-hidden="true">↗</span>
      </a>
    </header>
  );
}
