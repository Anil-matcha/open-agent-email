"use client";

import { useState, useRef, useEffect } from "react";
import { ChevronDown, Check } from "lucide-react";

export interface DropdownOption {
  value: string;
  label: string;
  sublabel?: string;
  badge?: string;
}

interface DropdownProps {
  options: DropdownOption[];
  value: string;
  onChange: (value: string) => void;
  label?: string;
  placeholder?: string;
  className?: string;
}

export function Dropdown({
  options,
  value,
  onChange,
  label,
  placeholder = "Select an option",
  className = "",
}: DropdownProps) {
  const [isOpen, setIsOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const selectedOption = options.find((opt) => opt.value === value);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  return (
    <div className={`relative ${className}`} ref={containerRef}>
      {label && (
        <label className="block text-[11px] font-medium text-zinc-400 mb-1">
          {label}
        </label>
      )}
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        className="w-full flex items-center justify-between gap-2 px-3 py-2 text-xs text-left bg-[#161a22] border border-zinc-800 hover:border-zinc-700 rounded-lg text-zinc-200 transition-colors focus:outline-none focus:border-zinc-500"
      >
        <span className="truncate">
          {selectedOption ? selectedOption.label : placeholder}
        </span>
        <ChevronDown
          className={`w-3.5 h-3.5 text-zinc-400 transition-transform duration-150 shrink-0 ${
            isOpen ? "rotate-180 text-zinc-200" : ""
          }`}
        />
      </button>

      {isOpen && (
        <div className="absolute z-50 left-0 mt-1 w-full min-w-[200px] bg-[#161a22] border border-zinc-800 rounded-lg shadow-xl py-1 text-xs max-h-60 overflow-y-auto animate-in fade-in-50 duration-100">
          {options.map((option) => {
            const isSelected = option.value === value;
            return (
              <button
                key={option.value}
                type="button"
                onClick={() => {
                  onChange(option.value);
                  setIsOpen(false);
                }}
                className={`w-full flex items-center justify-between px-3 py-2 text-left transition-colors ${
                  isSelected
                    ? "bg-zinc-800/80 text-white font-medium"
                    : "text-zinc-300 hover:bg-zinc-800/40 hover:text-white"
                }`}
              >
                <div className="truncate pr-2">
                  <div className="truncate">{option.label}</div>
                  {option.sublabel && (
                    <div className="text-[10px] text-zinc-400 font-mono truncate">
                      {option.sublabel}
                    </div>
                  )}
                </div>
                {isSelected && <Check className="w-3.5 h-3.5 text-blue-400 shrink-0" />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
