import type { Metadata } from 'next';
import type { Viewport } from 'next';
import './globals.css';

const title = 'Aayush Desai — Astrophysicist';
const description =
  'Aayush Desai studies compact stars, magnetic fields, time-domain astronomy, and astrophysical simulations at ISTA.';
const siteUrl = process.env.NEXT_PUBLIC_SITE_URL ?? 'https://aayushdesai.github.io';

const socialImage = new URL('/og.png', siteUrl).toString();

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),
  title,
  description,
  authors: [{ name: 'Aayush Desai' }],
  creator: 'Aayush Desai',
  keywords: [
    'Aayush Desai',
    'astrophysics',
    'white dwarfs',
    'time-domain astronomy',
    'magnetic stars',
    'astrophysical simulations',
  ],
  openGraph: {
    type: 'website',
    title,
    description,
    siteName: 'Aayush Desai',
    url: siteUrl,
    images: [{ url: socialImage, alt: 'Aayush Desai — Astrophysics, compact stars, changing skies' }],
  },
  twitter: {
    card: 'summary_large_image',
    title,
    description,
    images: [socialImage],
  },
};

export const viewport: Viewport = {
  themeColor: '#061426',
  colorScheme: 'light',
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
