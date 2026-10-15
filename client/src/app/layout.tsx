import type { Metadata } from "next";
import { Sidebar } from "@/components/Sidebar";
import { Header } from "@/components/Header";
import "./globals.css";

export const metadata: Metadata = {
  title: "Open Agent Email — The Open-Source Email Gateway for AI Agents",
  description: "Give AI agents two-way email inboxes with cryptographic SPF/DKIM verification, prompt-injection screening, and human-in-the-loop review.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark">
      <body className="bg-[#090c10] text-[#f0f6fc] antialiased selection:bg-indigo-500/30 selection:text-indigo-200">
        <div className="flex min-h-screen">
          <Sidebar />
          <div className="flex-1 flex flex-col min-w-0">
            <Header />
            <main className="flex-1 p-8 overflow-y-auto">
              <div className="max-w-7xl mx-auto space-y-8">
                {children}
              </div>
            </main>
          </div>
        </div>
      </body>
    </html>
  );
}
