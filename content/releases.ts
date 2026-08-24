export type ReleaseKind = 'Data' | 'Model' | 'Software';
export type ReleaseStatus = 'draft' | 'released' | 'superseded';

export type ReleaseFile = {
  name: string;
  description: string;
  format: string;
  size?: string;
  checksum?: string;
  url?: string;
};

export type ReleaseLink = {
  label: string;
  url: string;
};

export type ResearchRelease = {
  slug: string;
  title: string;
  kind: ReleaseKind;
  status: ReleaseStatus;
  version: string;
  date: string;
  summary: string;
  description: string[];
  topics: string[];
  creators: string[];
  license: string;
  versionDoi?: string;
  conceptDoi?: string;
  repository?: ReleaseLink;
  relatedPublication?: ReleaseLink;
  files: ReleaseFile[];
  provenance: string[];
  reproduce: string[];
  limitations: string[];
  citation?: string;
  links?: ReleaseLink[];
  socialImage?: string;
};

export const archiveStatus = 'work-in-progress' as const;

// Add a reviewed release here only when its public files and metadata are ready.
// The landing page and the release detail route are generated from this registry.
export const releases: ResearchRelease[] = [];

export function getPublishedReleases() {
  return releases.filter((release) => release.status !== 'draft');
}

export function getRelease(slug: string) {
  return getPublishedReleases().find((release) => release.slug === slug);
}
