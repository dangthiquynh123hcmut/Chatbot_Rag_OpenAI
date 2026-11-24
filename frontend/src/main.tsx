import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App.tsx";
import "katex/dist/katex.min.css";
import "./styles.css";

const container = document.getElementById("root");
if (!container) throw new Error("Không tìm thấy phần tử #root");

ReactDOM.createRoot(container).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
