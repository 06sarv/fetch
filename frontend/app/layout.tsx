import type { Metadata } from 'next'
import './style.css'
export const metadata: Metadata = { title: 'Fetch — Zero to Dataset Engine', description: 'Turn an ML objective into a reviewable, prepared dataset.' }
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>
}
