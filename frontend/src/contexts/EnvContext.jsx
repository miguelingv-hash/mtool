import { createContext, useContext, useState, useMemo } from "react";

const EnvContext = createContext(null);

export const EnvProvider = ({ children }) => {
  const [entorno, setEntorno] = useState(() => {
    return localStorage.getItem("sii_entorno") || "preproduccion";
  });

  const value = useMemo(
    () => ({
      entorno,
      setEntorno: (next) => {
        setEntorno(next);
        localStorage.setItem("sii_entorno", next);
      },
    }),
    [entorno],
  );

  return <EnvContext.Provider value={value}>{children}</EnvContext.Provider>;
};

export const useEnv = () => {
  const ctx = useContext(EnvContext);
  if (!ctx) throw new Error("useEnv must be used within EnvProvider");
  return ctx;
};
