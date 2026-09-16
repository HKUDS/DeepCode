import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { initI18n } from "./app/i18n";
import { App } from "./App";
import { tauriRuntime, configureNativeDialogs } from "./rpc/tauriRuntime";
import "./styles/tokens.css";

initI18n();
configureNativeDialogs();

const root = document.getElementById("root");

if (!root) {
  throw new Error("DeepCode desktop root element was not found");
}

createRoot(root).render(
  <StrictMode>
    <App runtime={tauriRuntime} />
  </StrictMode>,
);
