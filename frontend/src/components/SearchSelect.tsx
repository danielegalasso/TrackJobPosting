import { ChevronDown, X } from 'lucide-react';
import { useEffect, useId, useMemo, useRef, useState, type ComponentType } from 'react';

export interface SearchOption {
  value: string;
  label: string;
  /** Shown on the right of the row, e.g. how many postings. */
  hint?: string;
}

interface SearchSelectProps {
  label: string;
  placeholder: string;
  icon: ComponentType<{ className?: string; 'aria-hidden'?: boolean }>;
  options: SearchOption[];
  value: string;
  onChange: (value: string) => void;
}

/** Lower case without accents, so "turk" finds "Türkiye" and "eli" "Élancourt". */
function fold(text: string): string {
  return text.normalize('NFD').replace(/\p{Diacritic}/gu, '').toLowerCase();
}

/** A text box that searches a list and picks one of its entries. */
export function SearchSelect({ label, placeholder, icon: Icon, options, value, onChange }: SearchSelectProps) {
  const id = useId();
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const box = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const selected = options.find((option) => option.value === value);

  const shown = useMemo(() => {
    const wanted = fold(query.trim());
    return wanted ? options.filter((option) => fold(option.label).includes(wanted)) : options;
  }, [options, query]);

  useEffect(() => {
    const close = (event: MouseEvent) => {
      if (box.current && !box.current.contains(event.target as Node)) {
        setOpen(false);
        setQuery('');
      }
    };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, []);

  useEffect(() => {
    list.current?.children[active]?.scrollIntoView({ block: 'nearest' });
  }, [active]);

  const choose = (option: SearchOption) => {
    onChange(option.value);
    setQuery('');
    setOpen(false);
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setOpen(true);
      setActive((index) => Math.min(index + 1, shown.length - 1));
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setActive((index) => Math.max(index - 1, 0));
    } else if (event.key === 'Enter' && open && shown[active]) {
      event.preventDefault();
      choose(shown[active]);
    } else if (event.key === 'Escape') {
      setOpen(false);
      setQuery('');
    }
  };

  return (
    <div className="relative" ref={box}>
      <label className="sr-only" htmlFor={id}>
        {label}
      </label>
      <input
        id={id}
        role="combobox"
        aria-expanded={open}
        aria-controls={`${id}-list`}
        aria-autocomplete="list"
        autoComplete="off"
        placeholder={selected ? selected.label : placeholder}
        value={open ? query : (selected?.label ?? '')}
        onFocus={() => {
          setOpen(true);
          setActive(0);
        }}
        onChange={(event) => {
          setQuery(event.target.value);
          setOpen(true);
          setActive(0);
        }}
        onKeyDown={onKeyDown}
        className={`control pr-14 ${selected ? 'font-medium text-gray-900' : ''}`}
      />
      <Icon className="control-icon" aria-hidden />
      {value ? (
        <button
          type="button"
          onClick={() => onChange('')}
          aria-label={`Clear ${label.toLowerCase()}`}
          className="absolute right-8 top-1/2 -translate-y-1/2 rounded p-0.5 text-gray-400 hover:text-gray-700"
        >
          <X className="h-3.5 w-3.5" aria-hidden />
        </button>
      ) : (
        <ChevronDown className="pointer-events-none absolute right-8 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-gray-400" aria-hidden />
      )}
      {open && (
        <ul
          id={`${id}-list`}
          ref={list}
          role="listbox"
          aria-label={label}
          className="absolute z-20 mt-1 max-h-72 w-full overflow-y-auto rounded border border-gray-200 bg-white py-1 text-sm shadow-lg"
        >
          {shown.length === 0 ? (
            <li className="px-3 py-2 text-xs text-gray-500">No match</li>
          ) : (
            shown.map((option, index) => (
              <li
                key={option.value}
                role="option"
                aria-selected={option.value === value}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => choose(option)}
                onMouseEnter={() => setActive(index)}
                className={`flex cursor-pointer items-center justify-between gap-3 px-3 py-1.5 ${
                  index === active ? 'bg-emerald-50' : ''
                } ${option.value === value ? 'font-semibold text-brand' : 'text-gray-800'}`}
              >
                <span className="truncate">{option.label}</span>
                {option.hint && <span className="flex-shrink-0 text-xs text-gray-400">{option.hint}</span>}
              </li>
            ))
          )}
        </ul>
      )}
    </div>
  );
}
