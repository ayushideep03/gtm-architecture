import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";
import { LiveProvider, ToastProvider } from "./ui";
import { AuthGate } from "./auth";

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ToastProvider>
      <AuthGate>
        <LiveProvider>
          <App />
        </LiveProvider>
      </AuthGate>
    </ToastProvider>
  </React.StrictMode>,
);
