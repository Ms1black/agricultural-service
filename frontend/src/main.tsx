import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { setFavicon } from "./components/Jarvis";
import "./styles.css";

setFavicon();
createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>);
