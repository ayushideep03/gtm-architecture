import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";
import { LiveProvider, ToastProvider } from "./ui";

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ToastProvider>
      <LiveProvider>
        <App />
      </LiveProvider>
    </ToastProvider>
  </React.StrictMode>,
);
