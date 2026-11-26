import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";
import { ConfirmProvider, LiveProvider, ToastProvider } from "./ui";
import { AuthGate } from "./auth";

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ToastProvider>
      <ConfirmProvider>
      <AuthGate>
        <LiveProvider>
          <App />
        </LiveProvider>
      </AuthGate>
      </ConfirmProvider>
    </ToastProvider>
  </React.StrictMode>,
);
