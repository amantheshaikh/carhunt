import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "CarHunt",
  description: "Weekly used car deal tracker",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="min-h-screen bg-zinc-950 text-zinc-100" suppressHydrationWarning>{children}</body>
    </html>
  );
}
