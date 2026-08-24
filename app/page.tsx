import Link from 'next/link';
import { ScrollProgress } from './research-map';
import { SiteHeader } from './site-header';

const projects = [
  {
    number: '01',
    status: 'Current research',
    title: 'Rapidly rotating magnetic DQ white dwarfs',
    description:
      'Connecting multi-band light curves, carbon-rich spectra, and geometric models to understand a rare population of compact stellar remnants.',
    tags: ['Time-domain astronomy', 'Magnetic stars', 'Model comparison'],
  },
  {
    number: '02',
    status: 'Simulation work',
    title: 'Compact binaries in motion',
    description:
      'Using simulations to follow matter, magnetic fields, and outflows as white dwarfs interact and merge.',
    tags: ['Hydrodynamics', 'AREPO', 'Binary evolution'],
  },
  {
    number: '03',
    status: 'Survey work',
    title: 'Signals in a noisy sky',
    description:
      'Finding reliable variability in ZTF, ATLAS, WISE/NEOWISE, and high-speed photometry, while keeping the path from raw data to result clear.',
    tags: ['Surveys', 'Reproducibility', 'Scientific software'],
  },
];

const publications = [
  {
    year: '2026',
    title:
      'Circumstellar interaction in the extreme white dwarf merger remnant ZTF J1901+1458: A new class of white dwarf merger remnants with X-ray emission',
    authors: 'A. Desai et al.',
    venue: 'Astronomy & Astrophysics · published online 7 July 2026',
    note: 'First author',
    doi: '10.1051/0004-6361/202557035',
  },
  {
    year: '2026',
    title:
      'A Black Hole Star at Cosmic Noon: Extreme Balmer Break, Photospheric Continuum, and Broad Absorption by Thick Winds in a Little Red Dot at z = 1.7',
    authors: 'A. Torralba et al., including A. Desai',
    venue: 'The Astrophysical Journal Letters · 1005(2), L37',
    note: 'Seventh author of fifteen',
    doi: '10.3847/2041-8213/ae7bfd',
  },
  {
    year: '2026',
    title: 'Near-degeneracy effects in quadrupolar mixed modes',
    authors: 'B. R. B. Liagre, A. Desai, L. Einramhof & L. Bugnet',
    venue: 'Astronomy & Astrophysics · 707, A321',
    note: 'Second author of four',
    doi: '10.1051/0004-6361/202558023',
  },
  {
    year: '2026',
    title:
      'A half ring of ionized circumstellar material trapped in the magnetosphere of a white dwarf merger remnant',
    authors: 'A. A. Cristea et al., including A. Desai',
    venue: 'Astronomy & Astrophysics · 706, A188',
    note: 'Seventh author',
    doi: '10.1051/0004-6361/202556432',
  },
  {
    year: '2025',
    title: 'Fully Independent Response in Disordered Solids',
    authors: 'M. Zu, A. Desai & C. P. Goodrich',
    venue: 'Physical Review Letters · 134(23), 238201',
    note: 'Second author of three',
    doi: '10.1103/PhysRevLett.134.238201',
  },
  {
    year: '2020',
    title: 'A Highschooler’s Guide to GeV-Range Electromagnetism',
    authors: 'S. Chatterji et al., including A. Desai',
    venue: 'The Physics Educator · 2(3), 2050013',
    note: 'Second author',
    doi: '10.1142/S2661339520500134',
  },
];

const researchSteps = [
  {
    label: 'Observe',
    text: 'I start with the observation that does not quite fit: a changing light curve, an unusual spectrum, or a source that refuses the first explanation.',
  },
  {
    label: 'Simulate',
    text: 'I use numerical experiments to follow the matter, fields, and motion that a telescope cannot resolve directly.',
  },
  {
    label: 'Connect',
    text: 'Then I go back to the data. The result should be a physical story whose steps another person can follow.',
  },
];

export default function Home() {
  return (
    <main id="top">
      <ScrollProgress />

      <SiteHeader />

      <section className="hero" aria-labelledby="hero-title">
        <div className="hero-constellation" aria-hidden="true">
          <i className="star star-one" />
          <i className="star star-two" />
          <i className="star star-three" />
          <i className="hero-orbit orbit-one" />
          <i className="hero-orbit orbit-two" />
        </div>

        <div className="hero-copy">
          <p className="eyebrow"><span /> Astrophysicist · Vienna</p>
          <h1 id="hero-title">
            I study compact stars
            <span>and the changing sky.</span>
          </h1>
          <p className="hero-intro">
            I’m Aayush, an astrophysicist in Vienna. I work on magnetic white
            dwarfs, compact binaries, and time-domain astronomy, using observations
            and simulations side by side.
          </p>
          <div className="hero-actions">
            <a className="primary-button" href="#work">Explore my work <span>↓</span></a>
            <div className="hero-links" aria-label="Research profiles and documents">
              <a className="text-link" href="/Aayush-Alpesh-Desai-CV.pdf" target="_blank" rel="noreferrer">CV <span>↗</span></a>
              <a className="text-link" href="https://orcid.org/0009-0008-9877-5512" target="_blank" rel="noreferrer">ORCID <span>↗</span></a>
              <a className="text-link" href="https://github.com/aayushdesai" target="_blank" rel="noreferrer">GitHub <span>↗</span></a>
            </div>
          </div>
          <p className="hero-marginalia">
            <span>On my desk now</span>
            magnetic DQ light curves · white-dwarf merger outflows · more notebooks than necessary
          </p>
        </div>

        <figure className="portrait-wrap">
          <div className="portrait-frame">
            {/* The source is already sized and compressed for this fixed editorial crop. */}
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src="/aayush-desai.jpg"
              alt="Aayush Desai standing in front of a whiteboard"
              width={1800}
              height={1200}
              fetchPriority="high"
            />
          </div>
          <figcaption>
            <span className="availability-dot" />
            Researcher at ISTA
          </figcaption>
        </figure>

      </section>

      <section className="work-section section-shell" id="work" aria-labelledby="work-title">
        <div className="section-label">Research</div>
        <div className="section-heading work-heading">
          <h2 id="work-title">Observe, simulate, connect.</h2>
          <p>
            Most of my questions begin with a signal that does not quite make
            sense. I use whatever view helps—data, models, or simulations—and
            keep moving between them until the explanation holds together.
          </p>
        </div>

        <div className="method-note" aria-label="How I approach a research question">
          <p className="method-lead">
            For me, these are not separate jobs. They are a conversation.
          </p>
          <div className="method-sequence">
            {researchSteps.map((step) => (
              <p key={step.label}>
                <strong>{step.label}.</strong> {step.text}
              </p>
            ))}
          </div>
        </div>

        <h3 className="work-list-title">A few strands of current work</h3>
        <div className="project-list">
          {projects.map((project) => (
            <article className="project-row" key={project.number}>
              <span className="project-number">{project.number}</span>
              <div className="project-copy">
                <p>{project.status}</p>
                <h3>{project.title}</h3>
              </div>
              <p className="project-description">{project.description}</p>
              <ul aria-label={`${project.title} themes`}>
                {project.tags.map((tag) => <li key={tag}>{tag}</li>)}
              </ul>
            </article>
          ))}
        </div>

        <Link className="archive-gateway" href="/data-models/">
          <span>Data &amp; models</span>
          <h3>Work in progress.</h3>
          <p>The public release archive will open here. <i aria-hidden="true">→</i></p>
        </Link>
      </section>

      <section className="publications-section" id="publications" aria-labelledby="publications-title">
        <div className="section-shell publications-inner">
          <div className="section-label light">Publication record</div>
          <div className="section-heading publication-heading">
            <h2 id="publications-title">Publications</h2>
            <p>Six peer-reviewed articles, from magnetic white dwarfs to disordered solids.</p>
          </div>

          <div className="publication-actions">
            <p>Listed by year. My name appears as A. Desai.</p>
            <div>
              <a href="https://orcid.org/0009-0008-9877-5512" target="_blank" rel="noreferrer">ORCID 0009-0008-9877-5512 <span>↗</span></a>
              <a href="/Aayush-Alpesh-Desai-Publications.pdf" target="_blank" rel="noreferrer">Publication list PDF <span>↓</span></a>
            </div>
          </div>

          <div className="publication-list">
            {publications.map((publication) => (
              <a
                className="publication-row"
                href={`https://doi.org/${publication.doi}`}
                target="_blank"
                rel="noreferrer"
                key={publication.title}
              >
                <span className="publication-year">{publication.year}</span>
                <span className="publication-copy">
                  <strong>{publication.title}</strong>
                  <span className="publication-authors">{publication.authors}</span>
                  <small>{publication.venue}</small>
                  <code>doi:{publication.doi}</code>
                </span>
                <span className="publication-note">{publication.note}</span>
                <i aria-hidden="true">↗</i>
              </a>
            ))}
          </div>
        </div>
      </section>

      <section className="about-section section-shell" id="about" aria-labelledby="about-title">
        <div className="section-label">About</div>
        <div className="about-grid">
          <div className="about-title-wrap">
            <h2 id="about-title">A little more about me.</h2>
          </div>
          <div className="about-copy">
            <p className="about-lead">
              I like science that has to be assembled: a faint signal, an
              imperfect instrument, a model that almost explains it, and the
              patient work of finding out what remains.
            </p>
            <p>
              At the Institute of Science and Technology Austria, I work across
              observational astronomy, compact-object physics, and computation.
              My days move between telescopes and survey archives, simulation
              clusters and notebooks, collaborative manuscripts and the whiteboard.
            </p>
            <p>
              Outside the narrow lane of a project, I care about teaching,
              public astronomy, mathematical physics, and how we decide what a
              complicated system is really telling us.
            </p>
            <div className="about-links">
              <a href="mailto:adesai@ista.ac.at">Email <span>↗</span></a>
              <a href="https://orcid.org/0009-0008-9877-5512" target="_blank" rel="noreferrer">ORCID <span>↗</span></a>
              <a href="/Aayush-Alpesh-Desai-CV.pdf" target="_blank" rel="noreferrer">Curriculum vitae <span>↗</span></a>
              <a href="https://github.com/aayushdesai" target="_blank" rel="noreferrer">GitHub <span>↗</span></a>
            </div>
          </div>
        </div>
      </section>

      <section className="notes-section" id="notes" aria-labelledby="notes-title">
        <div className="notes-rule" aria-hidden="true"><span /></div>
        <div className="section-shell notes-inner">
          <div className="section-label">Notes</div>
          <div className="notes-construction">
            <h2 id="notes-title">Under construction.</h2>
          </div>
        </div>
      </section>

      <footer className="site-footer">
        <div className="footer-orbit" aria-hidden="true"><i /><i /></div>
        <p className="footer-quote">
          “Reserve your right to think, for even to think wrongly is better than not to think at all.”
        </p>
        <p className="footer-attribution">— Hypatia</p>
        <div className="footer-bottom">
          <a className="wordmark" href="#top">AD<span className="wordmark-dot">.</span></a>
          <p>An evolving notebook by Aayush Desai.</p>
          <a href="#top">Back to the beginning ↑</a>
        </div>
      </footer>
    </main>
  );
}
