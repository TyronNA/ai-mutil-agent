import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI Multi-Agent Dashboard",
  description: "Operations dashboard for the AI multi-agent pipeline",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen bg-background text-foreground antialiased" suppressHydrationWarning>
        {children}
      </body>
    </html>
  );
}
