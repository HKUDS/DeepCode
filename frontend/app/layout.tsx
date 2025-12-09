import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "DeepCode - Autonomous Research & Engineering Matrix",
  description: "Transform research papers directly into executable repositories via multi-agent LLM pipelines.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <head>
        {/* Use domestic CDN for fonts to ensure fast loading in China */}
        <link
          href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap"
          rel="stylesheet"
        />
      </head>
      <body className="bg-background text-text-main font-sans antialiased">
        {children}
      </body>
    </html>
  );
}