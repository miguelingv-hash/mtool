import "@/App.css";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { Toaster } from "@/components/ui/sonner";
import { EnvProvider } from "@/contexts/EnvContext";
import Layout from "@/components/Layout";
import Dashboard from "@/pages/Dashboard";
import UnitQuery from "@/pages/UnitQuery";
import BatchQuery from "@/pages/BatchQuery";
import History from "@/pages/History";

function App() {
  return (
    <EnvProvider>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<Dashboard />} />
            <Route path="/consulta" element={<UnitQuery />} />
            <Route path="/batch" element={<BatchQuery />} />
            <Route path="/historico" element={<History />} />
          </Route>
        </Routes>
      </BrowserRouter>
      <Toaster position="top-right" richColors closeButton />
    </EnvProvider>
  );
}

export default App;
