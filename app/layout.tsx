import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Article Topic Generator",
  description: "Create engaging article topics with AI",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
