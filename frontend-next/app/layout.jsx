import { Plus_Jakarta_Sans, JetBrains_Mono } from "next/font/google";
import { AuthProvider } from "@/hooks/use-auth";
import "@/app/globals.css";

// Self-hosted via next/font — downloaded once at build time and served as
// static assets from this app, not fetched from fonts.googleapis.com by
// each user's browser at runtime. Required for a deployment with no
// internet egress from the client network (banks commonly block this).
const jakarta = Plus_Jakarta_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-jakarta",
  display: "swap",
});
const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-jetbrains-mono",
  display: "swap",
});

export const metadata = {
  title: "ScenarioIQ",
  description: "AML diagnostic tool for OFSAA log analysis",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en" className={`${jakarta.variable} ${jetbrainsMono.variable}`}>
      <body>
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
