import { useEffect, useRef, useState } from "react";
import { ChevronDown as CaretDown, Check } from "lucide-react";
import { filterProvincias } from "../lib/provincias";

export default function ProvinciaCombobox({ value, onChange, testId }) {
  const [query, setQuery] = useState(value || "");
  const [open, setOpen] = useState(false);
  const [highlight, setHighlight] = useState(0);
  const ref = useRef(null);

  useEffect(() => setQuery(value || ""), [value]);
  useEffect(() => {
    const handler = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const options = filterProvincias(query);

  const select = (p) => {
    setQuery(p);
    onChange(p);
    setOpen(false);
  };

  const onKey = (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setOpen(true); setHighlight((h) => Math.min(options.length - 1, h + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setHighlight((h) => Math.max(0, h - 1)); }
    else if (e.key === "Enter") { e.preventDefault(); if (open && options[highlight]) select(options[highlight]); }
    else if (e.key === "Escape") { setOpen(false); }
  };

  return (
    <div className="relative" ref={ref}>
      <div className="relative">
        <input
          className="field-input pr-10"
          value={query}
          onChange={(e) => { setQuery(e.target.value); onChange(e.target.value); setOpen(true); setHighlight(0); }}
          onFocus={() => setOpen(true)}
          onKeyDown={onKey}
          placeholder="Ej. Asturias"
          data-testid={testId}
          autoComplete="off"
        />
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          className="absolute right-0 top-0 h-full px-3 text-zinc-500 hover:text-finapp-primary"
          tabIndex={-1}
          data-testid={`${testId}-toggle`}
        >
          <CaretDown size={16} />
        </button>
      </div>
      {open && (
        <div className="absolute z-50 left-0 right-0 mt-1 bg-white border border-zinc-200 shadow-lg max-h-64 overflow-auto" data-testid={`${testId}-dropdown`}>
          {options.length === 0 ? (
            <div className="px-3 py-2 text-sm text-zinc-500">Sin coincidencias</div>
          ) : (
            options.map((p, i) => (
              <button
                type="button"
                key={p}
                onClick={() => select(p)}
                onMouseEnter={() => setHighlight(i)}
                className={`w-full text-left px-3 py-2 text-sm flex items-center justify-between ${i === highlight ? "bg-finapp-primary/10" : "hover:bg-zinc-50"}`}
                data-testid={`${testId}-option-${p}`}
              >
                <span>{p}</span>
                {value === p && <Check size={14} className="text-finapp-primary" />}
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}
