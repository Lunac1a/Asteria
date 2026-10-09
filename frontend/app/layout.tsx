import type { Metadata } from "next";
import "./globals.css";
import { LocaleProvider } from "../lib/i18n";

export const metadata: Metadata = {
  title: "Asteria",
  description: "Asteria - AI Learning Copilot",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className="h-full antialiased"
    >
      <body className="min-h-full flex flex-col"><LocaleProvider>{children}</LocaleProvider></body>
    </html>
  );
}
