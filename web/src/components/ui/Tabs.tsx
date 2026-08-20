interface TabItem {
  id: string;
  label: string;
  icon?: React.ReactNode;
}

interface TabsProps<T extends string> {
  items: Array<TabItem & { id: T }>;
  active: T;
  onChange: (id: T) => void;
}

export function Tabs<T extends string>({ items, active, onChange }: TabsProps<T>) {
  return (
    <div className="inline-flex max-w-full items-center gap-1 overflow-x-auto rounded-lg bg-base p-1" role="tablist">
      {items.map((item) => (
        <button
          key={item.id}
          role="tab"
          aria-selected={active === item.id}
          onClick={() => onChange(item.id)}
          className={
            active === item.id
              ? "inline-flex items-center gap-2 whitespace-nowrap rounded-md bg-surface px-3 py-1.5 text-sm font-semibold text-ink shadow-sm"
              : "inline-flex items-center gap-2 whitespace-nowrap rounded-md px-3 py-1.5 text-sm font-medium text-soft transition-colors hover:text-ink"
          }
        >
          {item.icon}
          {item.label}
        </button>
      ))}
    </div>
  );
}