import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
  weight: ["400", "600"],
});

export const metadata: Metadata = {
  title: "AlquilerApp — Cobra a tiempo sin perseguir a nadie",
  description:
    "La app para propietarios en República Dominicana que cobra tus alquileres por WhatsApp y te avisa solo cuando necesitas decidir.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="es-DO" className={inter.variable}>
      <body>{children}</body>
    </html>
  );
}
