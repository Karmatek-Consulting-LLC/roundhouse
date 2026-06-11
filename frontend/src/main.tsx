import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { BrowserRouter } from "react-router-dom"
import "./index.css"
import { AuthProvider } from "@/lib/auth"
import App from "./App.tsx"

// Apply theme before first render to avoid flash
const savedTheme = localStorage.getItem("theme") ?? "dark"
const isDark = savedTheme === "dark" || (savedTheme === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches)
document.documentElement.classList.toggle("dark", isDark)

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <App />
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
)
