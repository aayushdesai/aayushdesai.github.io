import type { MetadataRoute } from 'next';
import { getPublishedReleases } from '@/content/releases';

const siteUrl = 'https://aayushdesai.github.io';

export const dynamic = 'force-static';

export default function sitemap(): MetadataRoute.Sitemap {
  const releasePages: MetadataRoute.Sitemap = getPublishedReleases().map((release) => ({
    url: `${siteUrl}/data-models/${release.slug}/`,
    lastModified: release.date,
    changeFrequency: 'yearly',
    priority: 0.7,
  }));

  return [
    {
      url: `${siteUrl}/`,
      changeFrequency: 'monthly',
      priority: 1,
    },
    {
      url: `${siteUrl}/data-models/`,
      changeFrequency: 'monthly',
      priority: 0.8,
    },
    ...releasePages,
  ];
}
